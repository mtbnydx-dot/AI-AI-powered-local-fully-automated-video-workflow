"""GPU layout profiles: validation, pool capacity and per-profile docker compose rendering."""
from collections import Counter

from .config import Instance, NodeConfig

# B200 MIG profiles: (compute slices of 7, memory slices of 8, GB). Confirm with `nvidia-smi mig -lgip`.
MIG_PROFILES = {"1g.23gb": (1, 1, 23), "1g.45gb": (1, 2, 45), "2g.45gb": (2, 2, 45),
                "3g.90gb": (3, 4, 90), "4g.90gb": (4, 4, 90), "7g.180gb": (7, 8, 180)}


def parse_device(device: int | str) -> tuple[int, str | None, int]:
    """7 -> (7, None, 0); "7/1g.23gb#1" -> (7, "1g.23gb", 1)."""
    if isinstance(device, int):
        return device, None, 0
    gpu, _, rest = str(device).partition("/")
    profile, _, index = rest.partition("#")
    return int(gpu), profile or None, int(index or 0)


def device_memory(node: NodeConfig, device: int | str) -> float:
    gpu, profile, _ = parse_device(device)
    return node.gpu_memory.get(gpu, node.gpu_memory_gb) if profile is None else MIG_PROFILES[profile][2]


def gpu_weight(node: NodeConfig, instance: Instance) -> float:
    """GPU-equivalents an instance occupies; MIG instances count their compute share."""
    total = 0.0
    for device in instance.devices:
        _, profile, _ = parse_device(device)
        total += 1.0 if profile is None else MIG_PROFILES[profile][0] / 7
    return round(total, 4)


def validate_node(node: NodeConfig) -> tuple[list[str], list[str]]:
    errors, warnings = [], []
    for gpu, layout in node.mig.items():
        if not 0 <= gpu < node.gpu_count:
            errors.append(f"MIG 布局引用了不存在的 GPU {gpu}")
        unknown = [p for p in layout if p not in MIG_PROFILES]
        if unknown:
            errors.append(f"GPU {gpu} 的 MIG 配置未知：{', '.join(unknown)}")
            continue
        compute = sum(MIG_PROFILES[p][0] for p in layout)
        memory = sum(MIG_PROFILES[p][1] for p in layout)
        if compute > 7 or memory > 8:
            errors.append(f"GPU {gpu} 的 MIG 切分超出容量（计算 {compute}/7，显存 {memory}/8）")
    if node.profile not in node.profiles:
        errors.append(f"当前 profile {node.profile} 未定义")
    for name, instances in node.profiles.items():
        used, ports = Counter(), Counter(i.port for i in instances)
        errors += [f"[{name}] 端口 {p} 重复" for p, n in ports.items() if n > 1]
        for instance in instances:
            where = f"[{name}] {instance.model}@{instance.devices}"
            model = node.models.get(instance.model)
            if model is None:
                errors.append(f"{where}: 未定义的模型服务")
                continue
            full, mig = 0, 0
            for device in instance.devices:
                gpu, profile, index = parse_device(device)
                used[(gpu, profile, index)] += 1
                if not 0 <= gpu < node.gpu_count:
                    errors.append(f"{where}: GPU {gpu} 不存在")
                elif profile is None:
                    full += 1
                    if node.mig.get(gpu):
                        errors.append(f"{where}: GPU {gpu} 已切 MIG，不能整卡使用")
                elif node.mig.get(gpu, []).count(profile) <= index:
                    errors.append(f"{where}: GPU {gpu} 没有声明第 {index + 1} 个 {profile} 实例")
                else:
                    mig += 1
            if model.gpus and (full != model.gpus or mig):
                errors.append(f"{where}: 该模型每实例需要 {model.gpus} 张整卡")
            if not model.gpus and (mig != 1 or full):
                errors.append(f"{where}: 该模型按单个 MIG 实例部署")
            memory = sum(device_memory(node, d) for d in instance.devices
                         if parse_device(d)[1] is None or parse_device(d)[1] in MIG_PROFILES)
            if memory < model.vram_gb:
                errors.append(f"{where}: 可用显存 {memory:g}GB 小于需求 {model.vram_gb:g}GB")
            if not model.verified:
                warnings.append(f"{instance.model}: 镜像与启动参数尚未通过 P0 基准验证（verified: false）")
        errors += [f"[{name}] 设备 {d} 被重复分配" for d, n in used.items() if n > 1]
        busy = {gpu for gpu, _, _ in used}
        idle = [g for g in range(node.gpu_count) if g not in busy]
        if idle:
            warnings.append(f"[{name}] GPU {idle} 未分配任务")
    return errors, list(dict.fromkeys(warnings))


def capacity(node: NodeConfig, profile: str) -> dict[str, int]:
    """Concurrent job slots per pool when the profile is running."""
    slots: Counter = Counter()
    for instance in node.profiles.get(profile, []):
        for pool in node.models[instance.model].pools:
            slots[pool] += 1
    return dict(slots)


def visible_devices(node: NodeConfig, instance: Instance) -> str:
    values = []
    for device in instance.devices:
        gpu, profile, index = parse_device(device)
        if profile is None:
            values.append(str(gpu))
        else:
            uuid = node.mig_uuids.get(f"{gpu}/{profile}#{index}")
            # Fallback "<gpu>:<mig index>" assumes MIG devices were created in the declared order.
            values.append(uuid or f"{gpu}:{_mig_index(node, gpu, profile, index)}")
    return ",".join(values)


def _mig_index(node: NodeConfig, gpu: int, profile: str, index: int) -> int:
    seen = -1
    for position, name in enumerate(node.mig.get(gpu, [])):
        seen += name == profile
        if name == profile and seen == index:
            return position
    return 0


def render_compose(node: NodeConfig, profile: str) -> dict:
    """docker compose document for the inference engines of one profile (agent/DCGM run separately)."""
    services = {}
    for number, instance in enumerate(node.profiles[profile]):
        model = node.models[instance.model]
        devices = visible_devices(node, instance)
        fill = {"port": instance.port, "num_gpus": max(len(instance.devices), 1), "devices": devices}
        services[f"{instance.model}-{number}".replace("_", "-")] = {
            "image": model.image,
            "command": model.command.format(**fill),
            "runtime": "nvidia",
            "network_mode": "host",
            "ipc": "host",
            "restart": "unless-stopped",
            "ulimits": {"memlock": -1, "stack": 67108864},
            "environment": {"NVIDIA_VISIBLE_DEVICES": devices, **model.env},
            "volumes": [f"{node.model_cache}:/models:ro", f"{node.store.root}:{node.store.root}"],
            "healthcheck": {"test": ["CMD-SHELL", f"curl -fsS http://127.0.0.1:{instance.port}{model.health_path} || exit 1"],
                            "interval": "30s", "timeout": "10s", "retries": 5, "start_period": "45m"},
            "labels": {"drama-factory.profile": profile, "drama-factory.pools": ",".join(model.pools)},
        }
    return {"name": "df-engines", "services": services}
