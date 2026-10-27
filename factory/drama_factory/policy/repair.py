"""Automatic rework: rule-based repair plans, validation of director (LLM) plans, applying a plan.

The local director may only propose. This module decides what is permitted, the controller executes,
and every change becomes a new versioned round; approved or earlier takes are never overwritten.
Story, dialogue and character identity are outside the vocabulary entirely, so they cannot be changed.
"""
from collections import Counter
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel

from ..protocol import h3
from ..protocol.manifest import ROUTES, Asset, Budget, Manifest, RepairAction, RouteCaps, Shot
from . import routing, taxonomy
from .review import Finding
from .spec import TakeSpec

PRIORITY = ("config", "story", "identity", "continuity", "dialogue", "motion", "artifact", "technical")
NO_TEXT = "No readable text, logos, subtitles, captions or watermarks."
SAME_FACES = "Keep every character's face, hairstyle and costume identical to the reference pictures."
SCRIPT_ONLY = "Characters speak only the scripted lines, word for word, one speaker at a time."
SCRIPT_ORDER = "Follow the scripted action order exactly; do not add, skip or reorder story beats."
PROPS_STAY = "Every prop stays in the same hand and position throughout; the scene layout does not change."
SLOW_MOTION = "Use slower, smaller and clearly readable movements."
ROLE_TEXT = {"face_front": "front face", "face_profile": "profile", "full_body": "full-body costume",
             "costume": "costume", "expression": "expression", "voice": "voice", "scene": "scene layout",
             "prop": "prop", "keyframe": "key pose", "motion": "motion", "style": "visual style"}


class RepairStep(BaseModel):
    action: RepairAction
    reason: str = ""
    fields: dict[str, str | list[str]] = {}  # revise_prompt: action/camera replace; constraints are appended
    assets: list[str] = []  # add_references / upgrade_mode
    route: str | None = None  # switch_route / upgrade_mode
    parts: list[dict] = []  # propose_split suggestion for the human


class RepairPlan(BaseModel):
    source: Literal["rules", "director", "human"] = "rules"
    rationale: str = ""
    steps: list[RepairStep]

    @property
    def terminal(self) -> RepairStep | None:
        """escalate / propose_split end automation for this shot and hand it to a human."""
        return next((s for s in self.steps if s.action in ("escalate", "propose_split")), None)


@dataclass
class RepairContext:
    manifest: Manifest
    shot: Shot
    spec: TakeSpec
    findings: list[Finding]  # failing findings of the round that just failed
    history: list[list[str]]  # failing codes of earlier rounds, oldest first
    rounds_used: int
    takes_used: int
    budget: Budget
    routes: dict[str, RouteCaps]
    infra_failed: bool = False
    bonus_rounds: int = 0


def limits(budget: Budget, shot: Shot, bonus_rounds: int = 0) -> tuple[int, int]:
    """(max rounds, max takes) for a shot, including rounds a human granted."""
    rounds = budget.max_rounds if shot.max_creative_retries is None else min(budget.max_rounds,
                                                                              shot.max_creative_retries + 1)
    return rounds + bonus_rounds, budget.max_takes + bonus_rounds * budget.candidates_per_round


def plan_repair(ctx: RepairContext) -> RepairPlan:
    max_rounds, max_takes = limits(ctx.budget, ctx.shot, ctx.bonus_rounds)
    if ctx.rounds_used >= max_rounds or ctx.takes_used >= max_takes:
        return escalation("budget_exhausted", f"已用 {ctx.rounds_used} 轮、{ctx.takes_used} 条候选，超出返工预算")
    if ctx.infra_failed:
        bigger = _next_route(ctx)
        if bigger:
            return _allowed(ctx, _plan("生成服务连续技术失败（如 OOM），换到更大的生成池",
                                       RepairStep(action="switch_route", route=bigger, reason="infrastructure")))
        return escalation("infrastructure", "生成服务连续技术失败，需要运维检查")
    codes = [f.code for f in ctx.findings]
    categories = Counter(taxonomy.category(code) for code in codes)
    primary = next((c for c in PRIORITY if categories[c]), "technical")
    seen = sum(any(taxonomy.category(code) == primary for code in past) for past in ctx.history)
    return _allowed(ctx, HANDLERS[primary](ctx, seen, codes))


def escalation(reason: str, rationale: str, parts: list[dict] | None = None) -> RepairPlan:
    action = "propose_split" if parts else "escalate"
    return RepairPlan(rationale=rationale, steps=[RepairStep(action=action, reason=reason, parts=parts or [])])


def _plan(rationale: str, *steps: RepairStep | None) -> RepairPlan:
    return RepairPlan(rationale=rationale, steps=[step for step in steps if step is not None])


def _allowed(ctx: RepairContext, plan: RepairPlan) -> RepairPlan:
    allowed = set(ctx.manifest.automation.allowed_actions) | {"escalate"}
    steps = [s for s in plan.steps if s.action in allowed]
    if not steps:
        return escalation("not_permitted", f"需要的修复动作未被任务包授权（{plan.rationale}）")
    return plan.model_copy(update={"steps": steps})


def _retry() -> RepairStep:
    return RepairStep(action="retry_seed", reason="新一轮使用新的随机种子")


def _revise(ctx: RepairContext, constraint: str | None = None, slow: bool = False) -> RepairStep | None:
    """Rule edits obey the package's editable_prompt_fields just like director plans do."""
    editable, prompt = set(ctx.manifest.automation.editable_prompt_fields), ctx.spec.prompt
    fields: dict[str, str | list[str]] = {}
    extra = [constraint] if constraint and constraint not in prompt.constraints else []
    if slow and SLOW_MOTION not in prompt.action and SLOW_MOTION not in prompt.constraints:
        if "action" in editable:
            fields["action"] = f"{prompt.action} {SLOW_MOTION}".strip()
        else:
            extra.append(SLOW_MOTION)
    if extra and "constraints" in editable:
        fields["constraints"] = extra
    return RepairStep(action="revise_prompt", fields=fields, reason="收紧提示词约束") if fields else None


def _assets(ctx: RepairContext) -> dict[str, Asset]:
    return {a.asset_id: a for a in ctx.manifest.assets}


def _locked(ctx: RepairContext, ids, kind: str) -> list[str]:
    assets, out = _assets(ctx), []
    for asset_id in ids:
        asset = assets.get(asset_id)
        if asset and asset.locked and asset.kind == kind and asset_id not in out:
            out.append(asset_id)
    return out


def identity_assets(ctx: RepairContext) -> list[str]:
    characters = {c.character_id: c for c in ctx.manifest.characters}
    return _locked(ctx, [a for c in ctx.shot.characters for a in characters[c].identity_assets], "image")


def voice_assets(ctx: RepairContext) -> list[str]:
    characters = {c.character_id: c for c in ctx.manifest.characters}
    speakers = dict.fromkeys(line.speaker for line in ctx.shot.dialogue)
    return _locked(ctx, [characters[s].voice_asset for s in speakers if characters[s].voice_asset], "audio")


def continuity_assets(ctx: RepairContext) -> list[str]:
    scene = next((s for s in ctx.manifest.scenes if s.scene_id == ctx.shot.scene_id), None)
    return _locked(ctx, [*ctx.shot.props, *(scene.assets if scene else [])], "image")


def _room(ctx: RepairContext, refs: list[str], kind: str) -> int:
    assets = _assets(ctx)
    counts = Counter(assets[a].kind for a in refs if a in assets)
    return max(0, min(h3.REF_LIMITS[kind] - counts[kind], h3.REF_LIMITS["total"] - sum(counts.values())))


def _next_route(ctx: RepairContext) -> str | None:
    return routing.escalate(ctx.spec.route, ctx.spec.mode, ctx.routes, ctx.spec.frames,
                            ctx.manifest.automation.max_route)


def _ref_route(ctx: RepairContext) -> str | None:
    cap = ROUTES.index(ctx.manifest.automation.max_route)
    options = [r for r in routing.supported("ref2va", ctx.routes, ctx.spec.frames) if ROUTES.index(r) <= cap]
    return next((r for r in options if ROUTES.index(r) >= ROUTES.index(ctx.spec.route)), options[0] if options else None)


def _upgrade(ctx: RepairContext, extra: list[str], reason: str) -> RepairStep | None:
    """FL2VA/T2VA -> Ref2VA: the first frame becomes <Picture 1>, locked bible images follow."""
    target = _ref_route(ctx)
    room = h3.REF_LIMITS["image"] - (1 if ctx.spec.first_frame else 0)
    assets = [a for a in extra if a != ctx.spec.first_frame][:room]
    if not target or not (assets or ctx.spec.first_frame):
        return None
    return RepairStep(action="upgrade_mode", route=target, assets=assets, reason=reason)


def _halves(shot: Shot) -> list[dict]:
    half = round(shot.duration_s / 2, 2)
    return [{"duration_s": half, "focus": "前半段：建立人物与道具位置"},
            {"duration_s": round(shot.duration_s - half, 2), "focus": "后半段：完成动作与对白"}]


def _config(ctx: RepairContext, seen: int, codes: list[str]) -> RepairPlan:
    return escalation("engine_config", "输出分辨率或帧率与请求不一致，属于推理服务配置问题，重试无效")


def _story(ctx: RepairContext, seen: int, codes: list[str]) -> RepairPlan:
    if seen:
        return escalation("story", "剧情连续偏离；自动修复无权改动剧情，需要导演处理")
    return _plan("剧情偏离：换种子并强调剧本顺序", _retry(), _revise(ctx, SCRIPT_ORDER))


def _identity(ctx: RepairContext, seen: int, codes: list[str]) -> RepairPlan:
    if seen >= 2:
        return escalation("identity", "多轮返工后仍有角色身份或服装漂移")
    steps, refs = [], identity_assets(ctx)
    if ctx.spec.mode != "ref2va":
        upgrade = _upgrade(ctx, refs, "改用 Ref2VA，由锁定的角色设定图约束身份")
        if upgrade:
            steps.append(upgrade)
    else:
        missing = [a for a in refs if a not in ctx.spec.references][:_room(ctx, ctx.spec.references, "image")]
        if missing:
            steps.append(RepairStep(action="add_references", assets=missing, reason="补充锁定的角色身份参考"))
        if seen or not missing:
            bigger = _next_route(ctx)
            if bigger:
                steps.append(RepairStep(action="switch_route", route=bigger, reason="升级到更高质量的生成池"))
    if not steps:
        if seen:
            return escalation("identity", "身份漂移且没有可追加的参考或更高的生成池")
        steps.append(_retry())
    steps.append(_revise(ctx, SAME_FACES, slow=ctx.shot.motion in ("medium", "high")))
    return _plan("角色身份或服装漂移", *steps)


def _continuity(ctx: RepairContext, seen: int, codes: list[str]) -> RepairPlan:
    if seen and ctx.shot.duration_s > 6:
        return escalation("continuity", "道具/场景连续性多次失败，建议拆成两个更短的镜头", parts=_halves(ctx.shot))
    if seen >= 2:
        return escalation("continuity", "道具/场景连续性多次失败")
    steps, extra = [], [a for a in continuity_assets(ctx) if a not in ctx.spec.references]
    if ctx.spec.mode == "ref2va":
        extra = extra[:_room(ctx, ctx.spec.references, "image")]
        if extra:
            steps.append(RepairStep(action="add_references", assets=extra, reason="补充道具/场景关键帧参考"))
    elif extra:
        upgrade = _upgrade(ctx, identity_assets(ctx) + extra, "改用 Ref2VA，加入道具/场景参考")
        if upgrade:
            steps.append(upgrade)
    if not steps:
        steps.append(_retry())
    steps.append(_revise(ctx, PROPS_STAY))
    return _plan("道具或场景不连续", *steps)


def _dialogue(ctx: RepairContext, seen: int, codes: list[str]) -> RepairPlan:
    if seen >= 2:
        return escalation("dialogue", "H3 原生音频多次未准确复现台词，建议转入配音 + 口型同步流程")
    if seen == 1:
        bigger = _next_route(ctx)
        if not bigger:
            return escalation("dialogue", "台词仍不准确且已在最高生成池")
        return _plan("台词仍不准确，升级生成池", RepairStep(action="switch_route", route=bigger, reason="升级生成池"),
                     _revise(ctx, SCRIPT_ONLY))
    steps = [_retry()]
    if ctx.spec.mode == "ref2va":
        voices = [a for a in voice_assets(ctx) if a not in ctx.spec.references]
        voices = voices[:_room(ctx, ctx.spec.references, "audio")]
        if voices:
            steps.append(RepairStep(action="add_references", assets=voices, reason="补充说话人声音参考"))
    steps.append(_revise(ctx, SCRIPT_ONLY))
    return _plan("台词或口型不符", *steps)


def _motion(ctx: RepairContext, seen: int, codes: list[str]) -> RepairPlan:
    if seen >= 2:
        if ctx.shot.duration_s > 6:
            return escalation("motion", "动作问题多次出现，建议拆成两个更短的镜头", parts=_halves(ctx.shot))
        return escalation("motion", "动作/肢体问题多次出现")
    bigger = _next_route(ctx) if seen else None
    first = RepairStep(action="switch_route", route=bigger, reason="升级生成池") if bigger else _retry()
    return _plan("动作或肢体问题：放慢动作幅度", first, _revise(ctx, slow=True))


def _artifact(ctx: RepairContext, seen: int, codes: list[str]) -> RepairPlan:
    if seen >= 2:
        bigger = _next_route(ctx)
        if not bigger:
            return escalation("artifact", "画面瑕疵多次出现且已在最高生成池")
        return _plan("画面瑕疵反复出现，升级生成池", RepairStep(action="switch_route", route=bigger, reason="升级生成池"))
    steps = [_retry()]
    if "TEXT_ARTIFACT" in codes:
        steps.append(_revise(ctx, NO_TEXT))
    return _plan("画面瑕疵：换种子重试", *steps)


def _technical(ctx: RepairContext, seen: int, codes: list[str]) -> RepairPlan:
    if seen >= 2:
        return escalation("technical", "生成结果多次技术性不合格（黑场/冻结/时长/音轨）")
    return _plan("生成结果技术性不合格：换种子重试", _retry())


HANDLERS = {"config": _config, "story": _story, "identity": _identity, "continuity": _continuity,
            "dialogue": _dialogue, "motion": _motion, "artifact": _artifact, "technical": _technical}


def check_plan(plan: RepairPlan, ctx: RepairContext, human: bool = False) -> list[str]:
    """Problems that make a plan unacceptable; empty means it may be executed."""
    problems = []
    automation = ctx.manifest.automation
    allowed = set(automation.allowed_actions) | {"escalate"}
    editable = {"action", "camera", "constraints", "description"} if human else set(automation.editable_prompt_fields)
    assets = _assets(ctx)
    scene = next((s for s in ctx.manifest.scenes if s.scene_id == ctx.shot.scene_id), None)
    own = {*identity_assets(ctx), *voice_assets(ctx), *ctx.shot.references, *ctx.shot.props,
           *(scene.assets if scene else []), ctx.shot.first_frame, ctx.shot.last_frame}
    for step in plan.steps:
        if not human and step.action not in allowed:
            problems.append(f"动作 {step.action} 未获授权")
        if step.action == "revise_prompt":
            extra = set(step.fields) - editable
            if extra:
                problems.append(f"不允许修改提示词字段：{', '.join(sorted(extra))}")
            for key, value in step.fields.items():  # dialogue must survive verbatim: checked in _check_spec
                if (key == "constraints") != isinstance(value, list):
                    problems.append(f"{key} 的取值类型错误（constraints 是要追加的句子列表，其余字段是文本）")
        if step.action in ("add_references", "upgrade_mode"):
            for asset_id in step.assets:
                asset = assets.get(asset_id)
                if asset is None:
                    problems.append(f"素材 {asset_id} 不存在")
                elif not human and not asset.locked:
                    problems.append(f"素材 {asset_id} 未锁定，不能自动加入")
                elif not human and asset_id not in own:
                    problems.append(f"素材 {asset_id} 不属于本镜头的角色、场景或道具")
        if step.action in ("switch_route", "upgrade_mode") and step.route is not None:
            if step.route not in ROUTES:
                problems.append(f"未知生成池 {step.route}")
            elif not human and ROUTES.index(step.route) > ROUTES.index(automation.max_route):
                problems.append(f"{step.route} 超出授权的最高生成池 {automation.max_route}")
    if not problems and not plan.terminal:
        try:
            apply_plan(ctx.spec, plan, ctx)
        except ValueError as exc:
            problems.append(str(exc))
    return problems


def apply_plan(spec: TakeSpec, plan: RepairPlan, ctx: RepairContext) -> TakeSpec:
    """New spec for the next round. Added references are appended, so existing tokens keep their numbers."""
    new, added = spec.model_copy(deep=True), []
    for step in plan.steps:
        if step.action == "revise_prompt":
            for key, value in step.fields.items():
                if key == "constraints":
                    new.prompt.constraints += [c for c in value if c not in new.prompt.constraints]
                else:
                    setattr(new.prompt, key, value)
        elif step.action == "add_references":
            added += [a for a in step.assets if a not in new.references and a not in added]
            new.references += [a for a in added if a not in new.references]
        elif step.action == "upgrade_mode" and new.mode != "ref2va":
            base = [new.first_frame] if new.first_frame else []
            new.mode, new.first_frame, new.last_frame = "ref2va", None, None
            new.references = list(dict.fromkeys(base + step.assets))
            added += new.references
            new.route = step.route or _ref_route(ctx) or new.route
        elif step.action == "switch_route" and step.route:
            new.route = step.route
    assets = _assets(ctx)
    known = [a for a in new.references if a in assets]
    slots = dict(zip(known, h3.ref_slots([assets[a].kind for a in known])))
    names = {c.character_id: c.name for c in ctx.manifest.characters}
    for asset_id in dict.fromkeys(added):
        if asset_id in slots:
            note = _ref_note(assets[asset_id], slots[asset_id], names, opening=asset_id == spec.first_frame)
            if note not in new.prompt.constraints:
                new.prompt.constraints.append(note)
    _check_spec(new, ctx)
    return new


def _ref_note(asset: Asset, slot: tuple[str, int], names: dict[str, str], opening: bool) -> str:
    token = f"<{slot[0]} {slot[1]}>"
    if opening:
        return f"The opening composition and lighting follow {token}."
    owner = names.get(asset.owner or "", asset.owner or "")
    role = ROLE_TEXT.get(asset.role, asset.role)
    return f"{token} is the {role} reference for {owner}." if owner else f"{token} is the {role} reference."


def _check_spec(spec: TakeSpec, ctx: RepairContext) -> None:
    caps = ctx.routes.get(spec.route)
    if caps is None or spec.mode not in caps.modes:
        raise ValueError(f"生成池 {spec.route} 不支持 {spec.mode}")
    if spec.frames > caps.max_frames:
        raise ValueError(f"{spec.frames} 帧超过 {spec.route} 上限 {caps.max_frames}")
    if (spec.mode == "ref2va") != bool(spec.references):
        raise ValueError("Ref2VA 必须有参考素材，其他模式不能带参考素材")
    assets = _assets(ctx)
    unknown = [a for a in spec.references if a not in assets]
    if unknown:
        raise ValueError(f"未知素材：{', '.join(unknown)}")
    kinds = [assets[a].kind for a in spec.references]
    counts = Counter(kinds)
    if any(counts[k] > h3.REF_LIMITS[k] for k in ("image", "video", "audio")) or len(kinds) > h3.REF_LIMITS["total"]:
        raise ValueError("参考素材数量超过 H3 Ref2VA 上限（图 9 / 视频 3 / 音频 3 / 合计 12）")
    used = set().union(*(h3.ref_tokens(text) for text in h3.prompt_texts(spec.prompt)))
    missing = used - set(h3.ref_slots(kinds))
    if missing:
        raise ValueError(f"提示词引用了不存在的参考：{', '.join(f'<{k} {n}>' for k, n in sorted(missing))}")
    scripted = [tag for text in h3.prompt_texts(ctx.shot.prompt) for tag in h3.dialogue_tags(text)]
    if [tag for text in h3.prompt_texts(spec.prompt) for tag in h3.dialogue_tags(text)] != scripted:
        raise ValueError("修复方案改动或删除了剧本台词；台词只能通过新版本任务包修改")


def director_brief(ctx: RepairContext, suggestion: RepairPlan) -> dict:
    """Everything the LLM director may look at; it answers with a RepairPlan JSON."""
    max_rounds, max_takes = limits(ctx.budget, ctx.shot, ctx.bonus_rounds)
    assets = _assets(ctx)
    scene = next((s for s in ctx.manifest.scenes if s.scene_id == ctx.shot.scene_id), None)
    usable = dict.fromkeys([*identity_assets(ctx), *voice_assets(ctx), *continuity_assets(ctx),
                            *(scene.assets if scene else [])])
    return {
        "shot": ctx.shot.model_dump(mode="json"),
        "current_spec": ctx.spec.model_dump(mode="json"),
        "findings": [f.model_dump(mode="json") for f in ctx.findings],
        "history": ctx.history,
        "budget": {"rounds_used": ctx.rounds_used, "takes_used": ctx.takes_used,
                   "max_rounds": max_rounds, "max_takes": max_takes},
        "allowed_actions": ctx.manifest.automation.allowed_actions,
        "editable_prompt_fields": ctx.manifest.automation.editable_prompt_fields,
        "max_route": ctx.manifest.automation.max_route,
        "routes": {name: caps.model_dump() for name, caps in ctx.routes.items()},
        "usable_assets": [assets[a].model_dump(mode="json") for a in usable if a in assets],
        "suggested_plan": suggestion.model_dump(mode="json"),
    }
