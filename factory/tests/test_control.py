import json
from pathlib import Path

from conftest import OPERATOR, PACKAGE, WORKER, perfect_probe, shot
from drama_factory.cluster.config import Instance, WatchdogConfig
from drama_factory.demo import demo_node, scenario_handlers
from drama_factory.node import backends
from drama_factory.node.agent import director_handler, generation_handler
from drama_factory.protocol import keys
from drama_factory.protocol.package import seal, verify_bundle

NODE = "b200-sim-01"


def events(harness, shot_id: str) -> list[dict]:
    return harness.shot(shot_id)["events"]


def test_full_episode_flow(harness, tmp_path):
    h = harness()
    assert h.import_package().json()["status"] == "imported"
    h.run()
    shots = h.shots()
    assert shots["S001"]["state"] == "approved"
    assert any(e["kind"] == "job_failed" and e["data"]["code"] == "OOM" for e in events(h, "S001"))
    assert (shots["S002"]["state"], shots["S002"]["round"], shots["S002"]["route"]) == ("approved", 2, "fast_ref")
    s002 = h.shot("S002")["shot"]["spec"]
    assert s002["mode"] == "ref2va" and s002["references"][0] == "s002_first"
    assert shots["S003"]["state"] == "needs_human"
    assert (shots["S004"]["state"], shots["S004"]["round"], shots["S004"]["route"]) == ("needs_human", 3, "native_ref")
    assert shots["S005"]["state"] == "approved"  # waited for S003's machine pass
    tasks = h.tasks()
    assert tasks["S003"]["reason"] == "final_check" and tasks["S004"]["reason"] == "budget_exhausted"
    assert [r["level"] for r in h.shot("S003")["reviews"]] == ["L0", "L1", "L2.asr", "L2.identity", "L3"]

    decision = h.http.post(f"/v1/human-tasks/{tasks['S003']['id']}/decision", headers=OPERATOR,
                           json={"action": "approve", "operator": "director-zhang"})
    assert decision.json()["shot_state"] == "approved"
    exported = h.http.post(f"/v1/packages/{PACKAGE}/export", headers=OPERATOR, json={}).json()
    bundle = Path(exported["path"])
    site_trust = {**keys.load_trust_dir(tmp_path / "keys")}  # studio.pub + site.pub
    files, _, kinds, _, _, issues = verify_bundle(bundle, site_trust)
    assert not issues and sorted(p for p in files if p.startswith("media/")) == [
        "media/S001.mp4", "media/S002.mp4", "media/S003.mp4", "media/S005.mp4"]
    result = json.loads((bundle / "results_manifest.json").read_text("utf-8"))
    assert result["summary"]["by_state"] == {"approved": 4, "needs_human": 1}
    assert shot(result, "S004")["human_task"]["reason"] == "budget_exhausted"
    assert shot(result, "S004")["last_findings"][0]["code"] == "DIALOGUE_MISMATCH"


def test_import_is_idempotent_and_versioned(harness, package_dir, studio):
    h = harness()
    assert h.import_package().json()["status"] == "imported"
    assert h.import_package().json()["status"] == "already_imported"
    manifest = json.loads((package_dir / "manifest.json").read_text("utf-8"))
    manifest["title"] = "changed"
    (package_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    seal(package_dir, studio[0])
    assert h.import_package().status_code == 409


def test_import_gates(harness, package_dir, tmp_path):
    h = harness()
    (package_dir / "props" / "card.png").write_bytes(b"\x89PNG\r\n\x1a\nchanged")
    rejected = h.import_package()
    assert rejected.status_code == 422 and rejected.json()["issues"][0]["code"] == "CHECKSUM"
    outside = h.http.post("/v1/packages/import", json={"path": str(tmp_path)}, headers=OPERATOR)
    assert outside.status_code == 403


def test_auth_and_metrics(harness):
    h = harness()
    assert h.http.get("/v1/status").status_code == 401
    assert h.http.get("/v1/status", headers=WORKER).status_code == 401
    assert h.http.post("/v1/jobs/claim", json={"node_id": NODE, "worker": "x", "pools": ["gen.vdn"]},
                       headers=OPERATOR).status_code == 401
    h.import_package()
    assert 'df_jobs{pool="gen.vdn",status="queued"}' in h.http.get("/metrics").text


def test_lease_expiry_requeues_and_rejects_late_results(harness):
    h = harness()
    h.import_package()
    h.agent.heartbeat()
    job = h.controller.claim(NODE, "w0", ["gen.vdn"])[0]
    h.clock.advance(h.site.scheduler.lease_s + 1)
    assert h.controller.tick()["expired"] == 1
    late = h.http.post(f"/v1/jobs/{job['job_id']}/complete", headers=WORKER,
                       json={"lease_token": job["lease_token"], "result": {}})
    assert late.status_code == 409
    h.clock.advance(60)
    h.agent.heartbeat()  # the long pause also marked the node offline
    again = h.controller.claim(NODE, "w1", ["gen.vdn"], limit=10)
    retried = next(j for j in again if j["job_id"] == job["job_id"])
    assert retried["attempt"] == 2 and retried["lease_token"] != job["lease_token"]


def test_pause_blocks_generation_but_reviews_drain(harness):
    h = harness()
    h.import_package()
    h.agent.heartbeat()
    job = h.controller.claim(NODE, "w0", ["gen.vdn"])[0]
    h.controller.complete_job(job["job_id"], job["lease_token"], {"output_key": job["payload"]["output_key"],
                                                                  "probe": perfect_probe(job["payload"])})
    h.controller.pause("maintenance")
    assert h.controller.claim(NODE, "w0", ["gen.vdn", "gen.fast_ref"]) == []
    assert h.controller.claim(NODE, "w1", ["review.l1"])  # in-flight work still gets reviewed
    h.controller.resume()
    assert h.controller.claim(NODE, "w0", ["gen.vdn", "gen.fast_ref"])


def test_hot_gpu_drains_node_until_operator_resumes(harness):
    h = harness()
    h.import_package()
    hot = {"gpus": [{"index": 5, "temperature_c": 93, "ecc_uncorrected": 0}], "disks": {}}
    h.controller.node_heartbeat(NODE, {"profile": "autonomous", "pools": {"gen.vdn": 1}, "telemetry": hot})
    h.controller.tick()
    assert h.controller.claim(NODE, "w0", ["gen.vdn"]) == []
    assert h.controller.node_heartbeat(NODE, {"profile": "autonomous", "telemetry": {}})["drain"]
    h.http.post(f"/v1/nodes/{NODE}/resume", headers=OPERATOR, json={}).raise_for_status()
    assert h.controller.claim(NODE, "w0", ["gen.vdn"])


def test_engine_misconfiguration_pauses_the_pool(harness, tmp_path):
    def wrong_fps(path):
        return {**backends.stub_probe(path), "fps": 30.0}

    node = demo_node(tmp_path / "store", tmp_path / "work")
    h = harness(watchdog=WatchdogConfig(min_disk_free_gb=0, failure_streak=3), node=node)
    broken = generation_handler(backends.SimulatedGenerator(render=False), h.store, tmp_path / "work", 1.0, wrong_fps)
    h.agent._override.update({"gen.vdn": broken, "gen.fast_ref": broken, "gen.native_ref": broken})
    h.agent.workers = h.agent._build_workers()
    h.import_package()
    h.run()
    h.controller.tick()
    status = h.http.get("/v1/status", headers=OPERATOR).json()
    assert "gen.vdn" in status["paused_pools"]
    assert h.tasks()["S001"]["reason"] == "engine_config"


def test_human_retry_with_operator_plan(harness):
    h = harness()
    h.import_package()
    h.run()
    task = h.tasks()["S004"]
    bad = h.http.post(f"/v1/human-tasks/{task['id']}/decision", headers=OPERATOR, json={
        "action": "retry", "operator": "zhang", "plan": {"steps": [{"action": "add_references", "assets": ["nope"]}]}})
    assert bad.status_code == 422
    good = h.http.post(f"/v1/human-tasks/{task['id']}/decision", headers=OPERATOR, json={
        "action": "retry", "operator": "zhang", "extra_rounds": 1,
        "plan": {"rationale": "改成静态近景", "steps": [{"action": "revise_prompt", "fields": {"camera": "Static close-up."}}]}})
    assert good.json()["shot_state"] == "generating"
    assert h.shot("S004")["shot"]["spec"]["prompt"]["camera"] == "Static close-up."
    h.run()
    assert h.shots()["S004"]["round"] == 4 and h.tasks()["S004"]["reason"] == "budget_exhausted"


def test_director_plans_are_validated_before_use(harness, tmp_path):
    def rogue(payload):
        return {"rationale": "rewrite the scene", "steps": [{"action": "revise_prompt",
                                                              "fields": {"description": "Lin slaps Ethan."}}]}

    node = demo_node(tmp_path / "store", tmp_path / "work")
    h = harness(mutate=lambda d: d.setdefault("automation", {}).update(director="llm"), node=node)
    h.agent._override["director"] = director_handler(backends.SimulatedDirector(rogue))
    h.agent.workers = h.agent._build_workers()
    h.import_package()
    h.run()
    kinds = [e["kind"] for e in events(h, "S002")]
    assert "director_consulted" in kinds and "plan_rejected" in kinds
    assert h.shots()["S002"]["state"] == "approved"  # rules took over
    assert h.shot("S002")["shot"]["spec"]["prompt"]["description"].startswith("Live-action")


def test_silent_director_falls_back_to_rules(harness):
    h = harness(mutate=lambda d: d.setdefault("automation", {}).update(director="llm"))
    for worker in h.agent.workers:  # director advertised in the heartbeat but never claims anything
        worker.handlers.pop("director", None)
    h.import_package()
    h.run()
    kinds = [e["kind"] for e in events(h, "S002")]
    assert "director_consulted" in kinds and "director_timeout" in kinds
    assert h.shots()["S002"]["state"] == "approved"


def test_unserved_review_levels_are_skipped_not_faked(harness):
    h = harness(review_levels=["L1", "L2.asr"])
    h.import_package()
    h.run()
    s003 = h.shot("S003")
    assert any(e["kind"] == "review_skipped" and e["data"]["levels"] == ["L2.identity"] for e in s003["events"])
    assert "L3" not in [r["level"] for r in s003["reviews"]]
    assert h.tasks()["S003"]["reason"] == "final_check"


def test_tick_survives_bad_telemetry_and_failing_steps(harness, monkeypatch):
    h = harness()
    h.import_package()
    junk = {"gpus": [{"index": 0, "temperature_c": "ERR!", "ecc_uncorrected": "x"}, "nonsense"],
            "disks": {"/mnt/factory": "full"}}
    h.controller.node_heartbeat(NODE, {"profile": "autonomous", "pools": {"gen.vdn": 1}, "telemetry": junk})
    first = h.controller.claim(NODE, "w0", ["gen.vdn"])[0]
    h.clock.advance(h.site.scheduler.lease_s + 1)
    report = h.controller.tick()
    assert report["expired"] == 1 and not report["errors"]

    def broken(now):
        raise RuntimeError("watchdog exploded")

    h.controller.node_heartbeat(NODE, {"profile": "autonomous", "pools": {"gen.vdn": 1}})
    h.clock.advance(60)
    second = h.controller.claim(NODE, "w0", ["gen.vdn"])[0]
    monkeypatch.setattr(h.controller, "_watch", broken)
    h.clock.advance(h.site.scheduler.lease_s + 1)
    report = h.controller.tick()
    assert report["expired"] == 1 and report["errors"][0].startswith("watchdog")  # leases still expire
    assert h.http.get("/v1/status", headers=OPERATOR).json()["tick"]["errors"]
    assert {first["job_id"], second["job_id"]}  # both leases went through expiry above


def test_job_cancelled_mid_tick_is_not_requeued(harness):
    from drama_factory.store import models as db
    h = harness()
    h.import_package()
    h.agent.heartbeat()
    job = h.controller.claim(NODE, "w0", ["gen.vdn"])[0]
    h.clock.advance(h.site.scheduler.lease_s + 1)
    # tick collected this id as expired, then an earlier item in the same tick superseded its take
    with h.controller.tx() as s:
        s.get(db.Job, job["job_id"]).status = "cancelled"
    assert h.controller._expire_lease(job["job_id"], h.clock()) is False
    with h.controller.tx() as s:
        assert s.get(db.Job, job["job_id"]).status == "cancelled"


def test_outputs_are_written_per_attempt(harness):
    h = harness()
    h.import_package()
    h.agent.heartbeat()
    job = h.controller.claim(NODE, "w0", ["gen.vdn"])[0]
    assert job["payload"]["output_key"].endswith("-a1.mp4")
    stale = {"output_key": job["payload"]["output_key"].replace("-a1", "-a9"), "probe": perfect_probe(job["payload"])}
    assert h.controller.complete_job(job["job_id"], job["lease_token"], stale)["reason"] == "bad_output"
    h.clock.advance(60)
    again = next(j for j in h.controller.claim(NODE, "w0", ["gen.vdn"], limit=10) if j["job_id"] == job["job_id"])
    assert again["payload"]["output_key"].endswith("-a2.mp4")
    good = {"output_key": again["payload"]["output_key"], "probe": perfect_probe(again["payload"])}
    assert h.controller.complete_job(again["job_id"], again["lease_token"], good)["accepted"]


def test_reject_clears_selection_and_flags_blocked_dependents(harness):
    h = harness(mutate=lambda d: shot(d, "S005").update(depends_on=["S004"]))
    h.import_package()
    h.run()
    assert h.shots()["S005"]["state"] == "blocked"
    task = h.tasks()["S004"]
    h.http.post(f"/v1/human-tasks/{task['id']}/decision", headers=OPERATOR,
                json={"action": "reject", "operator": "zhang"}).raise_for_status()
    assert any(e["kind"] == "dependency_rejected" for e in events(h, "S005"))
    final = h.tasks()["S003"]
    h.http.post(f"/v1/human-tasks/{final['id']}/decision", headers=OPERATOR,
                json={"action": "reject", "operator": "zhang"}).raise_for_status()
    assert h.shot("S003")["shot"]["approved_take_id"] is None
    exported = h.http.post(f"/v1/packages/{PACKAGE}/export", headers=OPERATOR, json={"media": "candidates"}).json()
    result = json.loads((Path(exported["path"]) / "results_manifest.json").read_text("utf-8"))
    assert shot(result, "S003")["selected_take"] is None and shot(result, "S005")["blocked_by"] == ["S004"]


def test_interrupted_profile_switch_resumes_after_restart(harness, tmp_path):
    import subprocess

    from drama_factory.node.agent import ControlClient, NodeAgent
    node = demo_node(tmp_path / "store", tmp_path / "work")
    layout = node.profiles["autonomous"]
    ref_heavy = [Instance(model="fasth3_omni", devices=[gpu], port=30010 + gpu) for gpu in range(6)] + layout[3:]
    node = node.model_copy(update={"profiles": {"autonomous": layout, "ref_heavy": ref_heavy},
                                   "switch_command": "docker compose up {profile}"})

    def hangs(command, **_):
        raise subprocess.TimeoutExpired(command, 3600)

    h = harness(node=node)
    h.agent.runner = hangs
    h.import_package()
    h.agent.heartbeat()
    h.http.post(f"/v1/nodes/{NODE}/profile", headers=OPERATOR, json={"profile": "ref_heavy"}).raise_for_status()
    h.agent.heartbeat()
    assert h.agent.step() == 0 and h.agent.profile == "autonomous"  # failed, but the agent is still alive
    state = json.loads((tmp_path / "work" / "agent-state.json").read_text("utf-8"))
    assert state == {"profile": "autonomous", "switching_to": "ref_heavy"}

    calls = []
    restarted = NodeAgent(node, ControlClient("http://testserver", "w", client=h.http), store=h.store,
                          handlers=h.agent._override, telemetry_fn=lambda: {"gpus": [], "disks": {}},
                          lease_interval=None,
                          runner=lambda command, **_: calls.append(command) or type("R", (), {"returncode": 0})())
    assert restarted.profile == "ref_heavy" and restarted.pending == "ref_heavy"
    restarted.heartbeat()  # reports the profile it is committed to, so the control plane agrees
    assert h.controller.claim(NODE, "w0", ["gen.fast_ref"])  # control plane already sees ref_heavy
    restarted.step()
    assert calls == [["docker", "compose", "up", "ref_heavy"]] and restarted.pending is None
    assert json.loads((tmp_path / "work" / "agent-state.json").read_text("utf-8")) == {"profile": "ref_heavy"}


def test_profile_switch_drains_then_reports(harness, tmp_path):
    node = demo_node(tmp_path / "store", tmp_path / "work")
    layout = node.profiles["autonomous"]
    ref_heavy = [Instance(model="fasth3_omni", devices=[gpu], port=30010 + gpu) for gpu in range(6)] + layout[3:]
    node = node.model_copy(update={"profiles": {"autonomous": layout, "ref_heavy": ref_heavy},
                                   "switch_command": "echo switch {profile}"})
    commands = []

    def runner(command, **_):
        commands.append(command)
        return type("Done", (), {"returncode": 0, "stderr": ""})()

    h = harness(node=node)
    h.agent.runner = runner
    h.import_package()
    h.agent.heartbeat()
    assert h.http.post(f"/v1/nodes/{NODE}/profile", headers=OPERATOR, json={"profile": "missing"}).status_code == 422
    h.http.post(f"/v1/nodes/{NODE}/profile", headers=OPERATOR, json={"profile": "ref_heavy"}).raise_for_status()
    assert h.controller.claim(NODE, "w0", ["gen.vdn"]) == []  # draining for the switch
    h.agent.heartbeat()
    assert h.agent.step() == 0 and commands == [["echo", "switch", "ref_heavy"]]
    assert h.agent.profile == "ref_heavy" and h.agent.switch_to is None
    status = h.http.get("/v1/status", headers=OPERATOR).json()
    assert status["nodes"][0]["profile"] == "ref_heavy" and status["nodes"][0]["pools"]["gen.fast_ref"] == 6
    for _ in range(10):  # ref_heavy has no native pool, so only run until the VDN shot is through
        h.clock.advance(60)  # S001's scripted OOM retry waits out its back-off
        h.agent.heartbeat()
        h.agent.step()
    assert h.shots()["S001"]["state"] == "approved"
