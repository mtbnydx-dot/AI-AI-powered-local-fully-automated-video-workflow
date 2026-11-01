"""Deterministic checks for scene-by-scene long-form script production."""

import math

from .checks import DEFAULT_DURATION, check_episode


def _issue(issues, code, message, episode_id="", shot_id="", suggested_fix="", severity="error"):
    issues.append({
        "severity": severity, "code": code, "message": message,
        "episode_id": episode_id, "shot_id": shot_id, "suggested_fix": suggested_fix,
    })


def _references(scene, season, episode_id, issues):
    """Check planned references before generating the full scene."""
    scene_id = scene.get("id", "")
    characters = {x["id"]: x for x in season.get("characters", []) if isinstance(x, dict) and isinstance(x.get("id"), str)}
    locations = {x.get("id") for x in season.get("locations", []) if isinstance(x, dict) and isinstance(x.get("id"), str)}
    props = {x.get("id") for x in season.get("props", []) if isinstance(x, dict) and isinstance(x.get("id"), str)}
    location_id = scene.get("location_id")
    if not isinstance(location_id, str) or location_id not in locations:
        _issue(issues, "unknown_location", f"Scene {scene_id}: unknown location_id {location_id!r}.", episode_id,
               suggested_fix="Use a location id registered in season.locations.")
    cast = scene.get("characters", [])
    if not isinstance(cast, list):
        _issue(issues, "cast_type", f"Scene {scene_id}: characters must be a list.", episode_id,
               suggested_fix="Use a list of {id, wardrobe_id} objects.")
        cast = []
    for member in cast:
        if not isinstance(member, dict):
            _issue(issues, "cast_type", f"Scene {scene_id}: each character must be an object.", episode_id,
                   suggested_fix="Use {id, wardrobe_id} for each character.")
            continue
        character_id = member.get("id")
        character = characters.get(character_id) if isinstance(character_id, str) else None
        if character is None:
            _issue(issues, "unknown_character", f"Scene {scene_id}: unknown character {character_id!r}.", episode_id,
                   suggested_fix="Use a character id registered in season.characters.")
            continue
        wardrobes = [x.get("id") for x in character.get("wardrobes", []) if isinstance(x, dict)]
        if member.get("wardrobe_id") not in wardrobes:
            _issue(issues, "unknown_wardrobe", f"Scene {scene_id}: unknown wardrobe {member.get('wardrobe_id')!r} for {character_id}.", episode_id,
                   suggested_fix="Use a wardrobe registered under this character.")
    prop_ids = scene.get("prop_ids", [])
    if not isinstance(prop_ids, list):
        _issue(issues, "props_type", f"Scene {scene_id}: prop_ids must be a list.", episode_id,
               suggested_fix="Use a list of registered prop ids, or an empty list.")
    else:
        for prop_id in prop_ids:
            if not isinstance(prop_id, str) or prop_id not in props:
                _issue(issues, "unknown_prop", f"Scene {scene_id}: unknown prop_id {prop_id!r}.", episode_id,
                       suggested_fix="Use a prop id registered in season.props.")


def _episode_content(episode, season):
    """Preserve the existing checks without the short-episode length warning."""
    episode_id = episode.get("id", "")
    try:
        issues = check_episode(episode, season)
    except (TypeError, AttributeError) as exc:
        return [{
            "severity": "error", "code": "shot_structure", "episode_id": episode_id,
            "shot_id": "", "message": f"Episode content has malformed fields: {exc}.",
            "suggested_fix": "Use string ids, lists for characters/props/dialogue, and objects for shots and cast members.",
        }]
    return [{
        **issue, "episode_id": episode_id,
        "suggested_fix": issue.get("suggested_fix", "Correct the reported shot field using the canonical season references and the planned action."),
    } for issue in issues if not (issue["code"] == "shot_count" and issue["severity"] == "warning")]


def _shot_layout(shots, episode_id, first_number, issues, scene_id=None):
    for offset, shot in enumerate(shots):
        if not isinstance(shot, dict):
            continue  # check_episode reports the malformed shot.
        shot_id = shot.get("id", "")
        expected_id = f"{episode_id}_s{first_number + offset:03d}"
        if shot_id != expected_id:
            _issue(issues, "shot_number", f"Expected shot id {expected_id}, got {shot_id!r}.", episode_id, str(shot_id),
                   f"Set this shot id to {expected_id}; preserve chronological list order.")
        if scene_id is not None and shot.get("scene_id") != scene_id:
            _issue(issues, "shot_scene_id", f"Shot scene_id must be {scene_id!r}.", episode_id, str(shot_id),
                   "Use the enclosing scene_plan.id for every shot in this scene.")
        duration = shot.get("duration")
        if isinstance(duration, (int, float)) and not isinstance(duration, bool) and math.isfinite(duration) and 0 < duration <= 6 and duration != DEFAULT_DURATION:
            _issue(issues, "long_shot_duration", f"Long-form shots require {DEFAULT_DURATION:g}s; got {duration:g}s.", episode_id, str(shot_id),
                   "Set duration to 5.875 seconds (141 frames at 24 fps).")


def check_long_plan(plan: dict, season: dict, episode_plan: dict, scene_count: int = 7, shots_per_scene: int = 12) -> list[dict]:
    """Check the episode's scene plan and its consecutive shot ranges."""
    issues = []
    episode_id = episode_plan.get("id", "")
    if not isinstance(plan, dict):
        _issue(issues, "plan_type", "The episode scene plan must be an object.", episode_id,
               suggested_fix="Return {id, title, synopsis, start_state, end_state, scenes}.")
        return issues
    if plan.get("id") != episode_id:
        _issue(issues, "plan_episode_id", f"Plan id must be {episode_id!r}.", episode_id,
               suggested_fix="Use the id from the season episode outline.")
    if episode_id not in [e.get("id") for e in season.get("episodes", []) if isinstance(e, dict)]:
        _issue(issues, "unknown_episode", f"Episode {episode_id!r} is not in the season outline.", episode_id,
               suggested_fix="Use the matching season.episodes entry as episode_plan.")
    scenes = plan.get("scenes")
    if not isinstance(scenes, list):
        _issue(issues, "scenes_type", "Plan scenes must be a list.", episode_id,
               suggested_fix=f"Return {scene_count} scene objects in story order.")
        return issues
    if len(scenes) != scene_count:
        _issue(issues, "scene_count", f"Plan has {len(scenes)} scenes; expected {scene_count}.", episode_id,
               suggested_fix=f"Plan exactly {scene_count} complete scenes.")
    seen = set()
    for index, scene in enumerate(scenes):
        if not isinstance(scene, dict):
            _issue(issues, "scene_type", f"Scene {index + 1} must be an object.", episode_id,
                   suggested_fix="Return a scene with its id, references, shot range and beats.")
            continue
        scene_id = scene.get("id")
        if not isinstance(scene_id, str) or not scene_id.strip():
            _issue(issues, "scene_id", f"Scene {index + 1} needs a nonempty string id.", episode_id,
                   suggested_fix="Assign a stable scene id and reuse it in the scene output.")
        elif scene_id in seen:
            _issue(issues, "duplicate_scene_id", f"Scene id {scene_id!r} is duplicated.", episode_id,
                   suggested_fix="Give each planned scene a distinct stable id.")
        else:
            seen.add(scene_id)
        first = index * shots_per_scene + 1
        last = first + shots_per_scene - 1
        if type(scene.get("first_shot_number")) is not int or type(scene.get("last_shot_number")) is not int or scene.get("first_shot_number") != first or scene.get("last_shot_number") != last:
            _issue(issues, "scene_shot_range", f"Scene {scene_id}: expected shot range {first}–{last}.", episode_id,
                   suggested_fix=f"Set first_shot_number={first} and last_shot_number={last}.")
        beats = scene.get("beats")
        if not isinstance(beats, list) or len(beats) != shots_per_scene:
            _issue(issues, "scene_beat_count", f"Scene {scene_id} needs exactly {shots_per_scene} beats.", episode_id,
                   suggested_fix="Plan one complete action/event beat for each numbered shot.")
        _references(scene, season, episode_id, issues)
    return issues


def check_scene(scene: dict, scene_plan: dict, episode_plan: dict, season: dict) -> list[dict]:
    """Check one generated scene against its plan and the existing shot contract."""
    issues = []
    episode_id = episode_plan.get("id", "")
    scene_id = scene_plan.get("id", "")
    if not isinstance(scene, dict):
        _issue(issues, "scene_type", "Generated scene must be an object.", episode_id,
               suggested_fix="Return {episode_id, scene_id, shots}.")
        return issues
    if scene.get("episode_id") != episode_id:
        _issue(issues, "scene_episode_id", f"Scene episode_id must be {episode_id!r}.", episode_id,
               suggested_fix="Use the enclosing episode_plan.id.")
    if scene.get("scene_id") != scene_id:
        _issue(issues, "scene_id", f"Scene id must be {scene_id!r}.", episode_id,
               suggested_fix="Copy scene_plan.id to scene_id.")
    first, last = scene_plan.get("first_shot_number"), scene_plan.get("last_shot_number")
    if type(first) is not int or type(last) is not int or first < 1 or last < first:
        _issue(issues, "scene_shot_range", "Scene plan has an invalid shot range.", episode_id,
               suggested_fix="Fix the numbered scene plan before generating this scene.")
        return issues
    shots = scene.get("shots")
    if not isinstance(shots, list):
        _issue(issues, "shots_type", "Scene shots must be a list.", episode_id,
               suggested_fix="Return one shot object for each planned shot number.")
        return issues
    if len(shots) != last - first + 1:
        _issue(issues, "scene_shot_count", f"Scene {scene_id} has {len(shots)} shots; expected {last - first + 1}.", episode_id,
               suggested_fix=f"Return shots {first} through {last}, each exactly once.")
    episode = {"id": episode_id, "title": episode_plan.get("title", episode_id), "shots": shots}
    issues.extend(_episode_content(episode, season))
    _shot_layout(shots, episode_id, first, issues, scene_id)
    return issues


def check_long_season(episodes: list[dict], season: dict, minimum_seconds: float = 3600) -> list[dict]:
    """Check final episode order, global ids and the strictly greater-than runtime."""
    issues = []
    if not isinstance(episodes, list):
        _issue(issues, "episodes_type", "Episodes must be a list.", suggested_fix="Supply the completed episodes in season order.")
        return issues
    if not 5 <= len(episodes) <= 10:
        _issue(issues, "season_episode_count", f"Season contains {len(episodes)} episodes; expected 5–10.",
               suggested_fix="Use 5–10 complete episodes for this long-form season.")
    expected_ids = [e.get("id") for e in season.get("episodes", []) if isinstance(e, dict)]
    actual_ids = [e.get("id") if isinstance(e, dict) else None for e in episodes]
    if actual_ids != expected_ids:
        _issue(issues, "season_episode_order", f"Episode ids/order {actual_ids!r} do not match the season outline {expected_ids!r}.",
               suggested_fix="Deliver every planned episode exactly once, in season.episodes order.")
    seen_episode_ids, seen_shot_ids, scene_owners = set(), set(), {}
    total_seconds = 0.0
    for episode in episodes:
        if not isinstance(episode, dict):
            _issue(issues, "episode_type", "Each episode must be an object.", suggested_fix="Supply a complete episode object.")
            continue
        episode_id = str(episode.get("id", ""))
        if episode_id in seen_episode_ids:
            _issue(issues, "duplicate_episode_id", f"Episode id {episode_id!r} is duplicated.", episode_id,
                   suggested_fix="Use each planned episode id exactly once.")
        seen_episode_ids.add(episode_id)
        issues.extend(_episode_content(episode, season))
        shots = episode.get("shots", [])
        if not isinstance(shots, list):
            continue
        _shot_layout(shots, episode_id, 1, issues)
        for shot in shots:
            if not isinstance(shot, dict):
                continue
            shot_id = str(shot.get("id", ""))
            if shot_id in seen_shot_ids:
                _issue(issues, "duplicate_global_shot_id", f"Shot id {shot_id!r} occurs more than once in the season.", episode_id, shot_id,
                       "Keep every epXX_sNNN id unique across the season.")
            seen_shot_ids.add(shot_id)
            scene_id = shot.get("scene_id")
            if isinstance(scene_id, str):
                owner = scene_owners.setdefault(scene_id, episode_id)
                if owner != episode_id:
                    _issue(issues, "duplicate_global_scene_id", f"Scene {scene_id!r} is also used by {owner}.", episode_id, shot_id,
                           "Use distinct scene ids across episodes; reuse location_id for the same physical set.")
            duration = shot.get("duration")
            if isinstance(duration, (int, float)) and not isinstance(duration, bool) and math.isfinite(duration) and duration > 0:
                total_seconds += duration
    if total_seconds <= minimum_seconds:
        _issue(issues, "season_duration", f"Planned runtime is {total_seconds:g}s; it must exceed {minimum_seconds:g}s.",
               suggested_fix="Add complete planned scenes; do not stretch individual shots beyond 5.875 seconds.")
    return issues
