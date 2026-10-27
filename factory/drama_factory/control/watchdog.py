"""Safety interlocks for unattended runs: stop taking new work before hardware or disks get hurt.

Hardware alarms (temperature, ECC, disk) latch and need an operator to resume. The review backlog
throttle is the only self-clearing alarm and uses high/low watermarks to avoid flapping.
"""
from dataclasses import dataclass, field

from ..cluster.config import WatchdogConfig


@dataclass(frozen=True)
class Alarm:
    code: str
    scope: str  # "site" | "node:<id>" | "pool:<name>"
    severity: str  # critical | warning
    action: str  # pause_site | drain_node | pause_pool | throttle | none
    message: str


@dataclass
class NodeHealth:
    node_id: str
    stale: bool
    gpus: list[dict] = field(default_factory=list)
    disks: dict[str, float] = field(default_factory=dict)  # path -> free GB


def _num(value) -> float | None:
    """Telemetry arrives over the network: anything that is not a number is treated as unknown."""
    if isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def evaluate(nodes: list[NodeHealth], review_backlog: int, streaks: dict[str, int], throttled: bool,
             cfg: WatchdogConfig, starving: dict[str, int] | None = None) -> list[Alarm]:
    """starving: queued jobs per pool that no active node currently serves."""
    alarms = [Alarm("POOL_NO_CAPACITY", f"pool:{pool}", "warning", "none",
                    f"{pool} 有 {count} 个任务排队，但没有在线节点提供该池；检查节点 profile 或 site.review_levels")
              for pool, count in sorted((starving or {}).items())]
    for node in nodes:
        scope = f"node:{node.node_id}"
        if node.stale:
            alarms.append(Alarm("NODE_SILENT", scope, "warning", "none",
                                f"{node.node_id} 超过 {cfg.heartbeat_timeout_s:g}s 无心跳；租约到期后任务会自动重排"))
            continue
        for gpu in node.gpus if isinstance(node.gpus, list) else []:
            if not isinstance(gpu, dict):
                continue
            temperature, ecc = _num(gpu.get("temperature_c")), _num(gpu.get("ecc_uncorrected"))
            if temperature is not None and temperature >= cfg.gpu_temp_c:
                alarms.append(Alarm("GPU_HOT", scope, "critical", "drain_node",
                                    f"{node.node_id} GPU{gpu.get('index')} 温度 {temperature:g}°C ≥ {cfg.gpu_temp_c:g}°C"))
            if ecc is not None and ecc >= cfg.ecc_uncorrected:
                alarms.append(Alarm("GPU_ECC", scope, "critical", "drain_node",
                                    f"{node.node_id} GPU{gpu.get('index')} 出现 {ecc:g} 个不可纠正 ECC 错误"))
        for path, free in (node.disks.items() if isinstance(node.disks, dict) else []):
            free = _num(free)
            if free is not None and free < cfg.min_disk_free_gb:
                alarms.append(Alarm("DISK_LOW", "site", "critical", "pause_site",
                                    f"{node.node_id}:{path} 剩余 {free:.0f}GB < {cfg.min_disk_free_gb:g}GB"))
    high = review_backlog >= cfg.review_backlog_high
    if high or (throttled and review_backlog > cfg.review_backlog_low):
        alarms.append(Alarm("REVIEW_BACKLOG", "site", "warning", "throttle",
                            f"待审核 {review_backlog} 个，暂停领取新的生成任务直到降到 {cfg.review_backlog_low} 以下"))
    for pool, streak in streaks.items():
        if streak >= cfg.failure_streak:
            alarms.append(Alarm("FAILURE_STREAK", f"pool:{pool}", "critical", "pause_pool",
                                f"{pool} 连续 {streak} 条技术性失败，疑似模型/服务异常"))
    return alarms
