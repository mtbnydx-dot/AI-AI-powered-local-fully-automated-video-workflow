"""Route shots across the three H3 tiers: vdn (VDN-H3), fast_ref (FastH3 Omni), native_ref (H3 Ref2VA)."""
from typing import Literal

from ..protocol.manifest import ROUTES, RouteCaps, Shot

Difficulty = Literal["simple", "normal", "hard"]


def difficulty(shot: Shot) -> Difficulty:
    cast = len(shot.characters)
    score = min(cast, 3) + (2 if cast >= 3 else 0)
    score += {"static": 0, "low": 0, "medium": 1, "high": 2}[shot.motion]
    score += 2 if shot.interaction else 0
    score += int(shot.dialogue_precision == "exact") + int(len(shot.dialogue) > 2) + int(shot.duration_s > 10)
    return "simple" if score <= 1 else "normal" if score <= 4 else "hard"


def desired_tier(shot: Shot) -> int:
    """0 = speed first, 1 = reference first, 2 = quality first (index into ROUTES)."""
    cast = len(shot.characters)
    if cast >= 3 or (shot.interaction and shot.motion == "high") or (
            shot.importance == "key" and difficulty(shot) == "hard"):
        return 2
    if shot.mode == "ref2va" or cast >= 2 or shot.dialogue_precision == "exact":
        return 1
    return 0


def supported(mode: str, routes: dict[str, RouteCaps], frames: int | None = None) -> list[str]:
    return [r for r in ROUTES if r in routes and mode in routes[r].modes
            and (frames is None or frames <= routes[r].max_frames)]


def initial_route(shot: Shot, routes: dict[str, RouteCaps], frames: int, max_route: str = "native_ref") -> str:
    options = supported(shot.mode, routes, frames)
    if not options:
        raise ValueError(f"站点没有支持 {shot.mode} / {frames} 帧的生成池")
    if shot.route != "auto":
        if shot.route not in options:
            raise ValueError(f"指定的生成池 {shot.route} 不支持 {shot.mode} / {frames} 帧")
        return shot.route
    cap = ROUTES.index(max_route)
    within = [r for r in options if ROUTES.index(r) <= cap]
    if not within:
        raise ValueError(f"automation.max_route={max_route} 低于 {shot.mode} 所需的生成池")
    tier = min(desired_tier(shot), cap)
    return next((r for r in within if ROUTES.index(r) >= tier), within[-1])


def escalate(route: str, mode: str, routes: dict[str, RouteCaps], frames: int,
             max_route: str = "native_ref") -> str | None:
    """Next more capable pool for the same mode, or None at the top of the ladder."""
    current, cap = ROUTES.index(route), ROUTES.index(max_route)
    return next((r for r in supported(mode, routes, frames) if current < ROUTES.index(r) <= cap), None)
