from pathlib import Path

from drama_factory.cluster import planner
from drama_factory.cluster.config import BackendConfig, Instance, ModelService, NodeConfig, WatchdogConfig, load_node
from drama_factory.cluster.profiles import capacity, gpu_weight, render_compose, validate_node
from drama_factory.control import watchdog

CONFIG = Path(__file__).resolve().parents[1] / "config"


def test_example_nodes_are_valid():
    first = load_node(CONFIG / "node-b200-01.example.yaml")
    errors, warnings = validate_node(first)
    assert not errors and warnings  # unverified engines are flagged until P0 passes
    assert capacity(first, "autonomous") == {"gen.native_ref": 1, "gen.fast_ref": 2, "gen.vdn": 1, "review.l3": 1,
                                             "director": 1, "review.l1": 1, "review.asr": 1}
    second = load_node(CONFIG / "node-b200-02.example.yaml")
    assert not validate_node(second)[0]
    assert capacity(second, "batch") == {"gen.vdn": 4, "gen.fast_ref": 4}
    assert first.store.root == "/mnt/factory"  # POSIX paths survive loading on Windows


def node_with(instances, mig=None, **models) -> NodeConfig:
    backend = BackendConfig(kind="simulated")
    defaults = {"gen": ModelService(pools=["gen.vdn"], gpus=1, vram_gb=130, backend=backend),
                "flash_next": ModelService(pools=["review.l3"], gpus=0, vram_gb=130, backend=backend)}
    return NodeConfig(node_id="n", control_url="http://x", models={**defaults, **models}, mig=mig or {},
                      profiles={"p": instances}, profile="p")


def test_layout_errors_are_caught():
    overlap = node_with([Instance(model="gen", devices=[0], port=1), Instance(model="gen", devices=[0], port=2)])
    assert any("重复分配" in e for e in validate_node(overlap)[0])
    mig = {7: ["3g.90gb", "4g.90gb", "1g.23gb"]}
    assert any("超出容量" in e for e in validate_node(node_with([], mig=mig))[0])
    whole_mig_gpu = node_with([Instance(model="gen", devices=[7], port=1)], mig={7: ["3g.90gb"]})
    assert any("不能整卡使用" in e for e in validate_node(whole_mig_gpu)[0])
    # Qwen3.8-Flash-Next NVFP4 needs ~130GB: it cannot live in the 90GB MIG slice.
    too_big = node_with([Instance(model="flash_next", devices=["7/3g.90gb"], port=1)], mig={7: ["3g.90gb"]})
    assert any("显存" in e for e in validate_node(too_big)[0])
    ports = node_with([Instance(model="gen", devices=[0], port=1), Instance(model="gen", devices=[1], port=1)])
    assert any("端口" in e for e in validate_node(ports)[0])


def test_compose_render_and_gpu_weight():
    node = load_node(CONFIG / "node-b200-01.example.yaml")
    services = render_compose(node, "autonomous")["services"]
    native = services["h3-ref2va-native-0"]
    assert native["environment"]["NVIDIA_VISIBLE_DEVICES"] == "0,1,2,3"
    assert "--num-gpus 4" in native["command"] and "--port 30010" in native["command"]
    assert services["qwen3-asr-6"]["environment"]["NVIDIA_VISIBLE_DEVICES"] == "7:2"  # fallback MIG index
    pinned = node.model_copy(update={"mig_uuids": {"7/1g.23gb#0": "MIG-abc"}})
    assert render_compose(pinned, "autonomous")["services"]["qwen3-asr-6"]["environment"]["NVIDIA_VISIBLE_DEVICES"] == "MIG-abc"
    assert gpu_weight(node, node.profiles["autonomous"][0]) == 4.0
    assert gpu_weight(node, node.profiles["autonomous"][4]) == round(3 / 7, 4)


def test_planner_needs_gain_persistence_and_dwell():
    profiles = {"autonomous": {"gen.native_ref": 1, "gen.fast_ref": 2, "gen.vdn": 1},
                "ref_heavy": {"gen.fast_ref": 6, "gen.vdn": 1}}
    backlog = {"gen.fast_ref": 6000.0}
    args = dict(sustain_s=1800, min_dwell_s=7200, gain=0.3)
    choice, state = planner.recommend("autonomous", profiles, {}, backlog, planner.PlannerState(), 10_000, 0, **args)
    assert choice is None and state.candidate == "ref_heavy"
    choice, state = planner.recommend("autonomous", profiles, {}, backlog, state, 10_100, 0, **args)
    assert choice is None  # not sustained long enough
    choice, state = planner.recommend("autonomous", profiles, {}, backlog, state, 12_000, 6_000, **args)
    assert choice is None  # switched too recently
    choice, _ = planner.recommend("autonomous", profiles, {}, backlog, state, 13_300, 0, **args)
    assert choice == "ref_heavy"
    # Native work queued and nobody else serves it: never leave a profile that can serve it.
    mixed = {"gen.fast_ref": 6000.0, "gen.native_ref": 900.0}
    assert planner.recommend("autonomous", profiles, {}, mixed, planner.PlannerState("ref_heavy", 0), 99_999, 0,
                             **args)[0] is None
    # ...unless another node covers native capacity.
    assert planner.recommend("autonomous", profiles, {"gen.native_ref": 1}, mixed,
                             planner.PlannerState("ref_heavy", 0), 99_999, 0, **args)[0] == "ref_heavy"


def test_watchdog_alarms():
    cfg = WatchdogConfig(min_disk_free_gb=100, failure_streak=3)
    hot = watchdog.NodeHealth("b200-01", False, [{"index": 3, "temperature_c": 91, "ecc_uncorrected": 0}],
                              {"/mnt/factory": 50.0})
    silent = watchdog.NodeHealth("b200-02", True)
    alarms = watchdog.evaluate([hot, silent], 10, {"gen.vdn": 3}, False, cfg, {"review.identity": 4})
    found = {(a.code, a.action) for a in alarms}
    assert found == {("GPU_HOT", "drain_node"), ("DISK_LOW", "pause_site"), ("NODE_SILENT", "none"),
                     ("FAILURE_STREAK", "pause_pool"), ("POOL_NO_CAPACITY", "none")}
    # Review backlog throttle has hysteresis: on at >= high, stays on until <= low.
    assert any(a.action == "throttle" for a in watchdog.evaluate([], 200, {}, False, cfg))
    assert any(a.action == "throttle" for a in watchdog.evaluate([], 120, {}, True, cfg))
    assert not watchdog.evaluate([], 120, {}, False, cfg)
    assert not watchdog.evaluate([], 50, {}, True, cfg)
