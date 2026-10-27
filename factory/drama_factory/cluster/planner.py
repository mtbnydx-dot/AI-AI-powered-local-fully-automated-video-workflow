"""Profile switching with hysteresis: re-split a node's GPUs only when a backlog imbalance persists.

Switching costs a drain, model reload and warmup (minutes to tens of minutes for H3), so the planner
needs a clear gain, a sustained condition and a minimum dwell time since the last switch.
"""
from dataclasses import dataclass

INF = float("inf")


@dataclass
class PlannerState:
    candidate: str = ""
    since: float = 0.0


def drain_seconds(backlog: dict[str, float], capacity: dict[str, int]) -> float:
    """Time to clear the backlog (in work-seconds per pool) if pools run in parallel."""
    worst = 0.0
    for pool, work in backlog.items():
        if work <= 0:
            continue
        slots = capacity.get(pool, 0)
        worst = max(worst, work / slots if slots else INF)
    return worst


def recommend(current: str, profiles: dict[str, dict[str, int]], others: dict[str, int],
              backlog: dict[str, float], state: PlannerState, now: float, last_switch: float,
              sustain_s: float, min_dwell_s: float, gain: float) -> tuple[str | None, PlannerState]:
    """Return (profile to switch to or None, new state). `others` is capacity on the rest of the site."""
    def site(profile: str) -> dict[str, int]:
        merged = dict(others)
        for pool, slots in profiles[profile].items():
            merged[pool] = merged.get(pool, 0) + slots
        return merged

    if current not in profiles:
        return None, PlannerState()
    costs = {name: drain_seconds(backlog, site(name)) for name in profiles}
    best = min(costs, key=lambda name: (costs[name], name != current))
    improves = costs[current] == INF and costs[best] < INF or costs[best] < (1 - gain) * costs[current]
    if best == current or not improves:
        return None, PlannerState()
    if state.candidate != best:
        return None, PlannerState(best, now)
    if now - state.since >= sustain_s and now - last_switch >= min_dwell_s:
        return best, PlannerState()
    return None, state
