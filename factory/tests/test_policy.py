import pytest

from conftest import edit_manifest, shot
from drama_factory.cluster.config import DEFAULT_ROUTES
from drama_factory.policy import repair, review, routing
from drama_factory.policy.repair import RepairContext, RepairPlan, RepairStep
from drama_factory.policy.review import Finding, ReviewReport
from drama_factory.policy.spec import TakeSpec
from drama_factory.protocol.manifest import ReviewPolicy, frames_of, validate


def get(manifest, shot_id):
    return next(s for s in manifest.shots if s.shot_id == shot_id)


def context(manifest, shot_id, codes, history=(), rounds=1, takes=1, spec=None, infra=False):
    target = get(manifest, shot_id)
    spec = spec or TakeSpec.initial(target, routing.initial_route(target, DEFAULT_ROUTES, frames_of(target)))
    return RepairContext(manifest=manifest, shot=target, spec=spec,
                         findings=[Finding(code=code, severity="blocker") for code in codes],
                         history=[list(h) for h in history], rounds_used=rounds, takes_used=takes,
                         budget=getattr(manifest.retry_policy, routing.difficulty(target)), routes=DEFAULT_ROUTES,
                         infra_failed=infra)


def actions(plan: RepairPlan) -> list[str]:
    return [step.action for step in plan.steps]


def report(level, verdict="pass", findings=(), **metrics):
    return ReviewReport(level=level, reviewer="t", verdict=verdict, score=0.9,
                        findings=[Finding(code=c, severity=s) for c, s in findings], metrics=metrics)


def test_difficulty_and_initial_routes(manifest, package_dir):
    expected = {"S001": ("simple", "vdn"), "S002": ("simple", "vdn"), "S003": ("normal", "fast_ref"),
                "S004": ("normal", "fast_ref"), "S005": ("simple", "vdn")}
    for shot_id, (level, route) in expected.items():
        target = get(manifest, shot_id)
        assert (routing.difficulty(target), routing.initial_route(target, DEFAULT_ROUTES, frames_of(target))) == (level, route)
    action = edit_manifest(package_dir, lambda d: shot(d, "S003").update(interaction=True, motion="high"))
    assert routing.initial_route(get(action, "S003"), DEFAULT_ROUTES, 145) == "native_ref"
    with pytest.raises(ValueError):
        routing.initial_route(get(manifest, "S003"), DEFAULT_ROUTES, 145, max_route="vdn")


def test_escalation_ladder():
    assert routing.escalate("fast_ref", "ref2va", DEFAULT_ROUTES, 145) == "native_ref"
    assert routing.escalate("vdn", "fl2va", DEFAULT_ROUTES, 145) is None  # only VDN serves FL2VA here
    assert routing.escalate("fast_ref", "ref2va", DEFAULT_ROUTES, 145, max_route="fast_ref") is None
    assert routing.escalate("fast_ref", "ref2va", DEFAULT_ROUTES, 350) == "native_ref"  # 350 > fast_ref max 345


def test_l0_machine_checks(manifest):
    target, policy = get(manifest, "S002"), manifest.review_policy
    expect = {"width": 768, "height": 1344, "frames": 124}
    good = {"decodable": True, "width": 768, "height": 1344, "fps": 24.0, "duration_s": 5.167, "has_audio": True,
            "black_s": 0.0, "longest_freeze_s": 0.0, "silence_s": 0.0, "max_volume_db": -0.05}
    clipped = review.l0_report(good, target, expect, policy)
    assert clipped.verdict == "pass" and [f.code for f in clipped.findings] == ["AUDIO_CLIPPING"]
    bad = {**good, "fps": 30.0, "black_s": 1.0, "black_ranges": [[0.0, 1.0]], "has_audio": False}
    result = review.l0_report(bad, target, expect, policy)
    assert result.verdict == "fail"
    assert {f.code for f in result.findings} == {"WRONG_FPS", "BLACK_FRAMES", "AUDIO_MISSING"}
    assert review.l0_report({"decodable": False}, target, expect, policy).findings[0].code == "CORRUPT_FILE"


def test_metrics_are_judged_centrally(manifest):
    s004, s003, policy = get(manifest, "S004"), get(manifest, "S003"), manifest.review_policy
    outcome = review.verdict([report("L2.asr", cer=0.3, transcript="原来你")], s004, policy, "t1")
    assert outcome.status == "fail" and outcome.findings[0].code == "DIALOGUE_MISMATCH"
    lenient = s004.model_copy(update={"review": s004.review.model_copy(update={"max_dialogue_cer": 0.5})})
    assert review.verdict([report("L2.asr", cer=0.3)], lenient, policy, "t1").status == "pass"
    missing = review.verdict([report("L2.identity", identity={"lin_qiao": None, "ethan": 0.8})], s003, policy, "t1",
                             {"lin_qiao": "Lin Qiao"})
    assert [f.code for f in missing.findings] == ["WRONG_CHARACTER"] and "Lin Qiao" in missing.findings[0].message


def test_uncertainty_escalates_to_l3_then_human(manifest):
    s001, s003, policy = get(manifest, "S001"), get(manifest, "S003"), manifest.review_policy
    assert review.verdict([report("L1")], s003, policy, "t").status == "need_l3"  # key shot
    assert review.verdict([report("L1"), report("L3")], s003, policy, "t").status == "pass"
    vague = report("L1", verdict="fail")  # "fail" without a locatable finding is not evidence
    assert review.verdict([vague], s001, policy, "t").status == "need_l3"
    outcome = review.verdict([vague, report("L3", verdict="uncertain")], s001, policy, "t")
    assert outcome.status == "pass" and outcome.needs_human
    never = ReviewPolicy(l3="never")
    assert review.verdict([vague], s001, never, "t").needs_human
    tolerant = ReviewPolicy(tolerate=["TEXT_ARTIFACT"], l3="never")
    assert review.verdict([report("L1", findings=[("TEXT_ARTIFACT", "major")])], s001, tolerant, "t").status == "pass"


def test_identity_repair_upgrades_fl2va_to_ref2va(manifest):
    ctx = context(manifest, "S002", ["IDENTITY_DRIFT"])
    plan = repair.plan_repair(ctx)
    assert actions(plan) == ["upgrade_mode", "revise_prompt"]
    assert not repair.check_plan(plan, ctx)
    spec = repair.apply_plan(ctx.spec, plan, ctx)
    assert (spec.mode, spec.route, spec.first_frame) == ("ref2va", "fast_ref", None)
    assert spec.references == ["s002_first", "lin_front", "lin_profile", "lin_body"]
    assert "The opening composition and lighting follow <Picture 1>." in spec.prompt.constraints
    assert "<Picture 2> is the front face reference for Lin Qiao." in spec.prompt.constraints
    # Ladder only (a simple shot's real budget is 2 rounds, which would escalate here).
    second = repair.plan_repair(context(manifest, "S002", ["IDENTITY_DRIFT"], history=[["IDENTITY_DRIFT"]],
                                        rounds=1, takes=2, spec=spec))
    assert actions(second)[0] == "switch_route" and second.steps[0].route == "native_ref"
    exhausted = repair.plan_repair(context(manifest, "S002", ["IDENTITY_DRIFT"], [["IDENTITY_DRIFT"]], 2, 4, spec))
    assert exhausted.terminal.reason == "budget_exhausted"


def test_dialogue_repair_ladder(manifest):
    ctx = context(manifest, "S004", ["DIALOGUE_MISMATCH"])
    first = repair.plan_repair(ctx)
    assert actions(first) == ["retry_seed", "add_references", "revise_prompt"]
    spec = repair.apply_plan(ctx.spec, first, ctx)
    assert spec.references == ["lin_front", "river_plate", "lin_voice"]
    assert "<Audio 1> is the voice reference for Lin Qiao." in spec.prompt.constraints
    second = repair.plan_repair(context(manifest, "S004", ["DIALOGUE_MISMATCH"], [["DIALOGUE_MISMATCH"]], 2, 2, spec))
    assert actions(second) == ["switch_route"] and second.steps[0].route == "native_ref"
    third = repair.plan_repair(context(manifest, "S004", ["DIALOGUE_MISMATCH"], [["DIALOGUE_MISMATCH"]] * 2, 2, 2))
    assert third.terminal.reason == "dialogue"


def test_budget_config_and_infrastructure(manifest):
    assert repair.plan_repair(context(manifest, "S004", ["ANATOMY_ERROR"], rounds=3, takes=3)).terminal.reason == \
        "budget_exhausted"
    assert repair.plan_repair(context(manifest, "S001", ["WRONG_FPS"])).terminal.reason == "engine_config"
    oom = repair.plan_repair(context(manifest, "S003", [], infra=True))
    assert actions(oom) == ["switch_route"] and oom.steps[0].route == "native_ref"
    s003 = get(manifest, "S003")
    native = TakeSpec.initial(s003, "native_ref")
    assert repair.plan_repair(context(manifest, "S003", [], infra=True, spec=native)).terminal.reason == "infrastructure"


def test_repeated_continuity_failure_proposes_split(package_dir):
    manifest = edit_manifest(package_dir, lambda d: shot(d, "S003").update(duration_s=8))
    first = repair.plan_repair(context(manifest, "S003", ["PROP_DISCONTINUITY"]))
    assert actions(first) == ["add_references", "revise_prompt"] and first.steps[0].assets == ["card_prop"]
    again = repair.plan_repair(context(manifest, "S003", ["PROP_DISCONTINUITY"], [["PROP_DISCONTINUITY"]], 2, 2))
    assert again.terminal.action == "propose_split" and len(again.terminal.parts) == 2


def test_director_plans_are_bounded(package_dir):
    manifest = edit_manifest(package_dir, lambda d: (d["assets"][7].update(locked=False),
                                                     d.setdefault("automation", {}).update(max_route="fast_ref")))
    ctx = context(manifest, "S003", ["IDENTITY_DRIFT"])

    def problems(*steps, human=False):
        return repair.check_plan(RepairPlan(source="human" if human else "director", steps=list(steps)), ctx, human)

    assert problems(RepairStep(action="revise_prompt", fields={"description": "A new story."}))
    keeps_line = ("Two-shot at the table. Ethan says: <d>[English] My card is frozen again. Can you get this "
                  "one?</d> Lin pays.")
    assert not problems(RepairStep(action="revise_prompt", fields={"description": keeps_line}), human=True)
    assert problems(RepairStep(action="revise_prompt", fields={"description": "A new story."}), human=True)
    assert problems(RepairStep(action="add_references", assets=["card_prop"]))  # not locked
    assert problems(RepairStep(action="add_references", assets=["s002_first"]))  # another shot's keyframe
    assert problems(RepairStep(action="switch_route", route="native_ref"))  # above max_route
    assert problems(RepairStep(action="revise_prompt", fields={"constraints": ["<d>[English] Pay me.</d>"]}))
    assert problems(RepairStep(action="revise_prompt", fields={"camera": "Match <Picture 9>."}))
    assert not problems(RepairStep(action="add_references", assets=["lin_profile"]),
                        RepairStep(action="revise_prompt", fields={"camera": "Slower push-in."}))


def test_repairs_never_touch_dialogue(package_dir):
    def move_line_into_action(d):
        prompt = shot(d, "S004")["prompt"]
        prompt["description"] = prompt["description"].replace(
            " She looks straight ahead, voice low and controlled: <d>[Chinese] 原来你爱的是额度。</d>", "")
        prompt["action"] = "She looks straight ahead and says: <d>[Chinese] 原来你爱的是额度。</d>"

    manifest = edit_manifest(package_dir, move_line_into_action)
    assert not [i for i in validate(manifest) if i.level == "error"]  # a valid package
    ctx = context(manifest, "S004", ["ANATOMY_ERROR"])
    rogue = RepairPlan(source="director", steps=[RepairStep(action="revise_prompt", fields={"action": "She turns slowly."})])
    assert any("台词" in p for p in repair.check_plan(rogue, ctx))
    rules = repair.plan_repair(ctx)  # slowing the motion appends to the action and keeps the line
    assert not repair.check_plan(rules, ctx)
    assert "<d>[Chinese] 原来你爱的是额度。</d>" in repair.apply_plan(ctx.spec, rules, ctx).prompt.action


def test_rule_plans_follow_editable_fields(package_dir):
    manifest = edit_manifest(package_dir, lambda d: (
        d.setdefault("automation", {}).update(editable_prompt_fields=["constraints", "camera"]),
        shot(d, "S004").update(motion="high")))
    ctx = context(manifest, "S004", ["ANATOMY_ERROR"])
    plan = repair.plan_repair(ctx)
    revise = next(step for step in plan.steps if step.action == "revise_prompt")
    assert "action" not in revise.fields and repair.SLOW_MOTION in revise.fields["constraints"]
    assert not repair.check_plan(plan, ctx) and plan.terminal is None


def test_dialogue_doubt_goes_to_a_human(manifest):
    s003, s004, policy = get(manifest, "S003"), get(manifest, "S004"), manifest.review_policy
    asr_down = report("L2.asr", verdict="uncertain", error="ENGINE_ERROR")
    outcome = review.verdict([report("L1"), asr_down], s004, policy, "t")
    assert outcome.status == "pass" and outcome.needs_human  # L3 cannot listen, so no L3 detour
    key = review.verdict([report("L1"), asr_down, report("L3")], s003, policy, "t")
    assert key.status == "pass" and key.needs_human  # a visual L3 pass does not clear dialogue doubt
    silent = review.verdict([report("L1"), report("L2.asr")], s004, policy, "t")  # "pass" with no CER measured
    assert silent.needs_human
    partial = review.verdict([report("L2.identity", identity={"ethan": 0.9})], s003, policy, "t",
                             {"lin_qiao": "Lin Qiao"})
    assert partial.status == "fail" and partial.findings[0].code == "WRONG_CHARACTER"


def test_unauthorised_actions_escalate(package_dir):
    manifest = edit_manifest(package_dir, lambda d: d.setdefault("automation", {}).update(allowed_actions=["retry_seed"]))
    plan = repair.plan_repair(context(manifest, "S002", ["IDENTITY_DRIFT"]))
    assert plan.terminal.reason == "not_permitted"
