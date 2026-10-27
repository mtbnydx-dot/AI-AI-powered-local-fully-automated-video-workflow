"""Single-machine rehearsal, no GPU needed: seal a sample package, run the control plane and a simulated
8xB200 node in-process, and walk every path (technical retry, automatic repair, L3, human queue,
dependencies) before exporting a signed result bundle.
"""
import json
import struct
import time
import warnings
import wave
import zlib
from collections import Counter
from pathlib import Path

from .cluster.config import (BackendConfig, Instance, ModelService, NodeConfig, SchedulerConfig, SiteConfig,
                             StoreConfig)
from .control.api import create_app
from .control.controller import Controller
from .node import backends, probe
from .node.agent import ControlClient, NodeAgent, director_handler, generation_handler, review_handler
from .protocol import keys
from .protocol.package import seal
from .storage import FileStore
from .store.db import make_sessions

NO_TEXT = "No readable text, logos, subtitles, captions or watermarks."


class Clock:
    """Wall clock that the demo can fast-forward through retry back-offs."""

    def __init__(self):
        self.offset = 0.0

    def __call__(self) -> float:
        return time.time() + self.offset

    def advance(self, seconds: float) -> None:
        self.offset += seconds


def tiny_png(rgb: tuple[int, int, int], size: int = 16) -> bytes:
    raw = b"".join(b"\x00" + bytes(rgb) * size for _ in range(size))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data))

    header = struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")


def tiny_wav(path: Path, seconds: float = 0.5) -> None:
    with wave.open(str(path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(16000)
        out.writeframes(b"\x00\x00" * int(16000 * seconds))


def write_demo_package(root: Path) -> Path:
    """A five-shot episode in the epp/1 format; see ARCHITECTURE.md for the field meanings."""
    files = {"characters/lin_qiao/front.png": (232, 196, 170), "characters/lin_qiao/profile.png": (226, 190, 165),
             "characters/lin_qiao/full_body.png": (210, 180, 150), "characters/ethan/front.png": (240, 210, 180),
             "scenes/riverside/plate.png": (20, 30, 60), "props/card.png": (150, 20, 40),
             "shots/S002/first.png": (40, 50, 80)}
    for rel, rgb in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_bytes(tiny_png(rgb))
    for rel in ("characters/lin_qiao/voice.wav", "characters/ethan/voice.wav"):
        tiny_wav(root / rel)

    def asset(asset_id, path, kind, role, owner=None):
        return {"asset_id": asset_id, "path": path, "kind": kind, "role": role, "owner": owner}

    style = "Live-action cinematic realism with a restrained 35mm look."
    manifest = {
        "package_id": "DEMO_EP01_V1", "project_id": "DEMO", "episode_id": "EP01", "version": 1,
        "title": "富姐的学费 · 示例集", "created_by": "studio-gpt", "created_at": "2026-10-28T00:00:00Z",
        "distribution": {"regions": ["CN", "SG", "MY"], "platforms": ["douyin", "kuaishou"]},
        "negative_prompt": "blurry, deformed hands, extra fingers, readable text, watermark",
        "characters": [
            {"character_id": "lin_qiao", "name": "Lin Qiao",
             "appearance": "28-year-old Chinese woman, oval face, dark almond eyes, a small beauty mark under the "
                           "left eye, straight shoulder-length black hair",
             "costume": "cream tailored blazer, black silk blouse, pearl studs, burgundy structured handbag",
             "voice": "warm, slightly low Beijing Mandarin", "voice_asset": "lin_voice",
             "identity_assets": ["lin_front", "lin_profile", "lin_body"]},
            {"character_id": "ethan", "name": "Ethan",
             "appearance": "27-year-old tall white Australian man, wavy sandy-blond hair, blue-grey eyes, light stubble",
             "costume": "cream linen shirt, navy trousers", "voice": "relaxed Australian English",
             "voice_asset": "ethan_voice", "identity_assets": ["ethan_front"]}],
        "scenes": [{"scene_id": "riverside", "assets": ["river_plate"],
                    "description": "Elegant Brisbane riverside restaurant at night, city lights on the river"}],
        "assets": [
            asset("lin_front", "characters/lin_qiao/front.png", "image", "face_front", "lin_qiao"),
            asset("lin_profile", "characters/lin_qiao/profile.png", "image", "face_profile", "lin_qiao"),
            asset("lin_body", "characters/lin_qiao/full_body.png", "image", "full_body", "lin_qiao"),
            asset("lin_voice", "characters/lin_qiao/voice.wav", "audio", "voice", "lin_qiao"),
            asset("ethan_front", "characters/ethan/front.png", "image", "face_front", "ethan"),
            asset("ethan_voice", "characters/ethan/voice.wav", "audio", "voice", "ethan"),
            asset("river_plate", "scenes/riverside/plate.png", "image", "scene", "riverside"),
            asset("card_prop", "props/card.png", "image", "prop"),
            asset("s002_first", "shots/S002/first.png", "image", "keyframe")],
        "shots": [
            {"shot_id": "S001", "order": 1, "duration_s": 4, "mode": "t2va", "scene_id": "riverside",
             "prompt": {"description": f"{style} Night establishing shot of a riverside restaurant; city lights "
                                       "shimmer on the river.", "camera": "Slow lateral dolly from left to right.",
                        "constraints": [NO_TEXT], "soundscape": "Distant river traffic and soft restaurant chatter."}},
            {"shot_id": "S002", "order": 2, "duration_s": 5, "mode": "fl2va", "first_frame": "s002_first",
             "characters": ["lin_qiao"], "scene_id": "riverside",
             "prompt": {"description": f"{style} Lin Qiao walks along the riverside promenade toward the "
                                       "restaurant entrance, keeping her cream blazer and burgundy handbag.",
                        "camera": "Medium tracking shot at walking pace.", "constraints": [NO_TEXT],
                        "soundscape": "Heels on stone, a light river breeze."}},
            {"shot_id": "S003", "order": 3, "duration_s": 6, "mode": "ref2va", "importance": "key",
             "characters": ["ethan", "lin_qiao"], "scene_id": "riverside",
             "references": ["lin_front", "ethan_front", "river_plate"], "props": ["card_prop"],
             "dialogue": [{"speaker": "ethan", "language": "English",
                           "text": "My card is frozen again. Can you get this one?"}],
             "prompt": {"description": f"{style} Medium two-shot at the restaurant table. <Picture 1> is Lin Qiao "
                                       "and <Picture 2> is Ethan; the room matches <Picture 3>. Ethan glances at an "
                                       "unbranded card, smiles awkwardly and says: <d>[English] My card is frozen "
                                       "again. Can you get this one?</d> Lin pauses, then taps her own card on a "
                                       "terminal with a blank screen.",
                        "camera": "Static two-shot, eye level.", "constraints": [NO_TEXT],
                        "soundscape": "Glassware, a short payment confirmation tone."}},
            {"shot_id": "S004", "order": 4, "duration_s": 4, "mode": "ref2va", "characters": ["lin_qiao"],
             "scene_id": "riverside", "references": ["lin_front", "river_plate"], "dialogue_precision": "exact",
             "dialogue": [{"speaker": "lin_qiao", "language": "Chinese", "text": "原来你爱的是额度。"}],
             "prompt": {"description": f"{style} Close medium shot. <Picture 1> is Lin Qiao in the corridor that "
                                       "matches <Picture 2>. She looks straight ahead, voice low and controlled: "
                                       "<d>[Chinese] 原来你爱的是额度。</d> Then she turns and walks away.",
                        "camera": "Slow push-in with small amplitude.", "constraints": [NO_TEXT],
                        "soundscape": "Measured footsteps, light rain."}},
            {"shot_id": "S005", "order": 5, "duration_s": 3, "mode": "t2va", "depends_on": ["S003"],
             "prompt": {"description": f"{style} The empty terrace after dinner; one chair left askew, rain on the "
                                       "river.", "constraints": [NO_TEXT], "soundscape": "Light rain on water."}},
        ],
    }
    (root / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return root


def demo_node(store_root: Path, work_dir: Path, generator: BackendConfig | None = None) -> NodeConfig:
    """The recommended 4 + 2 + 1 + MIG layout ("autonomous"), with simulated engines."""
    gen = generator or BackendConfig(kind="simulated")
    review = BackendConfig(kind="simulated-review")
    models = {
        "h3_ref2va_native": ModelService(pools=["gen.native_ref"], gpus=4, vram_gb=400, backend=gen),
        "fasth3_omni": ModelService(pools=["gen.fast_ref"], gpus=1, vram_gb=140, backend=gen),
        "vdn_h3": ModelService(pools=["gen.vdn"], gpus=1, vram_gb=130, backend=gen),
        "qwen38_27b": ModelService(pools=["review.l3", "director"], gpus=0, vram_gb=80, backend=review),
        "qwen3_vl_8b": ModelService(pools=["review.l1"], gpus=0, vram_gb=30, backend=review),
        "qwen3_asr": ModelService(pools=["review.asr"], gpus=0, vram_gb=10, backend=review),
        "identity_cv": ModelService(pools=["review.identity"], gpus=0, vram_gb=8, backend=review),
    }
    layout = [Instance(model="h3_ref2va_native", devices=[0, 1, 2, 3], port=30010),
              Instance(model="fasth3_omni", devices=[4], port=30014),
              Instance(model="fasth3_omni", devices=[5], port=30015),
              Instance(model="vdn_h3", devices=[6], port=30016),
              Instance(model="qwen38_27b", devices=["7/3g.90gb"], port=30020),
              Instance(model="qwen3_vl_8b", devices=["7/2g.45gb"], port=30021),
              Instance(model="qwen3_asr", devices=["7/1g.23gb#0"], port=30022),
              Instance(model="identity_cv", devices=["7/1g.23gb#1"], port=30023)]
    return NodeConfig(node_id="b200-sim-01", control_url="http://testserver", store=StoreConfig(root=str(store_root)),
                      work_dir=str(work_dir), mig={7: ["3g.90gb", "2g.45gb", "1g.23gb", "1g.23gb"]},
                      models=models, profiles={"autonomous": layout}, profile="autonomous")


def scenario_handlers(node: NodeConfig, store, render: bool) -> dict:
    """Scripted outcomes: S001 hits one OOM, S002 drifts in round 1, S004 never says its line right."""
    attempts: Counter = Counter()

    def fail(payload):
        attempts[payload["take_id"]] += 1
        return "OOM" if payload["shot_id"] == "S001" and attempts[payload["take_id"]] == 1 else None

    def shot_of(payload):
        return payload["shot"]["shot_id"], payload["round"]

    def l1(payload):
        if shot_of(payload) == ("S002", 1):
            return {"verdict": "fail", "score": 0.4, "findings": [
                {"code": "IDENTITY_DRIFT", "severity": "blocker", "t_start": 2.5, "t_end": 4.0,
                 "message": "第 2.5 秒起林乔的脸型和痣的位置与参考不一致", "suggestion": "加入正脸与侧脸参考"}]}
        return None

    def identity(payload):
        if shot_of(payload) == ("S002", 1):
            return {"metrics": {"identity": {"lin_qiao": 0.31}}}
        return None

    def asr(payload):
        if shot_of(payload)[0] == "S004":
            return {"metrics": {"cer": 0.6, "transcript": "原来你爱的是。", "expected": "原来你爱的是额度。"}}
        return None

    generator = backends.SimulatedGenerator(fail=fail, render=render)
    probe_fn = probe.probe_video if generator.render else backends.stub_probe
    work = Path(node.work_dir)
    gen = generation_handler(generator, store, work, 1.0, probe_fn)
    return {"gen.native_ref": generation_handler(generator, store, work, 4.0, probe_fn),
            "gen.fast_ref": gen, "gen.vdn": gen,
            "review.l1": review_handler(backends.SimulatedReviewer("L1", l1), store, work),
            "review.asr": review_handler(backends.SimulatedReviewer("L2.asr", asr), store, work),
            "review.identity": review_handler(backends.SimulatedReviewer("L2.identity", identity), store, work),
            "review.l3": review_handler(backends.SimulatedReviewer("L3"), store, work),
            "director": director_handler(backends.SimulatedDirector())}


def drain(controller: Controller, agent: NodeAgent, clock: Clock, max_steps: int = 400) -> None:
    for _ in range(max_steps):
        agent.heartbeat()
        if agent.step():
            continue
        queues = controller.status()["queues"]
        if not any(counts.get("queued", 0) + counts.get("leased", 0) for counts in queues.values()):
            return
        clock.advance(60)  # skip retry back-off
        controller.tick()
    raise RuntimeError("模拟没有收敛")


def run_demo(workdir: Path, render: bool = True, say=print) -> dict:
    with warnings.catch_warnings():  # starlette warns about its own httpx-based TestClient
        warnings.simplefilter("ignore")
        from fastapi.testclient import TestClient
    workdir = workdir.resolve()
    if workdir.exists() and any(workdir.iterdir()):
        raise FileExistsError(f"{workdir} 非空；请换一个目录")
    (workdir / "trusted_keys").mkdir(parents=True)
    studio_id = keys.generate(workdir / "keys" / "studio")
    keys.generate(workdir / "keys" / "site")
    (workdir / "trusted_keys" / "studio.pub").write_bytes((workdir / "keys" / "studio.pub").read_bytes())
    package = write_demo_package(workdir / "inbox" / "DEMO_EP01_V1")
    digest = seal(package, keys.load_private(workdir / "keys" / "studio.key"))
    say(f"1. 云端封装任务包 DEMO_EP01_V1（签名 key {studio_id}，摘要 {digest[:12]}…）")

    site = SiteConfig(site_id="demo-site", database_url=f"sqlite:///{(workdir / 'factory.db').as_posix()}",
                      store=StoreConfig(root=str(workdir / "store")), trusted_keys_dir=str(workdir / "trusted_keys"),
                      signing_key=str(workdir / "keys" / "site.key"), staging_dir=str(workdir / "staging"),
                      export_dir=str(workdir / "exports"), import_roots=[str(workdir / "inbox")],
                      worker_token="demo-worker", operator_token="demo-operator",
                      scheduler=SchedulerConfig(lease_s=600))
    clock, store = Clock(), FileStore(workdir / "store")
    controller = Controller(make_sessions(site.database_url), store, site, clock=clock)
    operator = {"Authorization": "Bearer demo-operator"}
    with TestClient(create_app(controller, site.worker_token, site.operator_token)) as http:
        imported = http.post("/v1/packages/import", json={"path": str(package)}, headers=operator)
        imported.raise_for_status()
        say(f"2. 离线站点校验并导入：{imported.json()}")
        node = demo_node(workdir / "store", workdir / "work")
        agent = NodeAgent(node, ControlClient("http://testserver", site.worker_token, client=http), store=store,
                          handlers=scenario_handlers(node, store, render),
                          telemetry_fn=lambda: {"gpus": [], "disks": {}}, lease_interval=None)
        drain(controller, agent, clock)
        view = http.get("/v1/packages/DEMO_EP01_V1", headers=operator).json()
        say("3. 自动生产结束：")
        for shot in view["shots"]:
            say(f"   {shot['shot_id']}  {shot['state']:<12} 轮次 {shot['round']}  候选 {shot['takes']}  生成池 {shot['route']}")
        tasks = http.get("/v1/human-tasks", headers=operator).json()["tasks"]
        say(f"4. 人工队列 {len(tasks)} 项：" + "；".join(f"{t['shot'].split('/')[-1]}={t['reason']}" for t in tasks))
        for task in tasks:
            if task["reason"] == "final_check":
                http.post(f"/v1/human-tasks/{task['id']}/decision", headers=operator,
                          json={"action": "approve", "operator": "demo-director", "note": "关键镜头终审通过"}
                          ).raise_for_status()
                say(f"   导演终审通过 {task['shot'].split('/')[-1]}")
        drain(controller, agent, clock)
        exported = http.post("/v1/packages/DEMO_EP01_V1/export", headers=operator, json={"media": "approved"})
        exported.raise_for_status()
        result = exported.json()
    say(f"5. 结果包已签名导出：{result['path']}")
    say("   " + json.dumps(result["summary"], ensure_ascii=False))
    return {"workdir": str(workdir), "package": view, "export": result}
