"""Small, deterministic checks for a text-only episode before handoff."""

import math
import re


FPS = 24
DEFAULT_DURATION = 141 / FPS
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
_CHINESE = re.compile(r"[\u3400-\u9fff]")
_UNFINISHED = re.compile(r"尚未|还未|未完成|动作未完|没来得及|刚要|正要|到一半|伸到一半|走到一半")
_CONTINUED = re.compile(r"上一镜|上个镜头|承接|延续|继续(?:刚才|上镜|动作)|接着(?:走|拿|伸|抬|打开|转身)")


def h3_frame_count(duration: float, fps: int = FPS) -> int:
    """Match videopipe's built-in H3 rounding: 5 + 17n frames."""
    requested = max(5, math.ceil(duration * fps))
    return requested + (5 - requested) % 17


def spoken_length(text: str) -> int:
    """Count spoken characters, excluding punctuation and whitespace."""
    return sum(character.isalnum() for character in text)


def check_episode(episode: dict, season: dict) -> list[dict]:
    """Report concrete errors and editorial warnings; never contact a model."""
    issues: list[dict] = []

    def issue(code: str, message: str, shot_id: str = "", severity: str = "error") -> None:
        issues.append({"severity": severity, "shot_id": shot_id, "code": code, "message": message})

    def english(value: object, field: str, shot_id: str) -> None:
        if not isinstance(value, str) or not value.strip():
            issue("missing_english", f"{field} must contain an English description.", shot_id)
        elif _CHINESE.search(value):
            issue("non_english_instruction", f"{field} contains Chinese; keep Chinese only in dialogue.", shot_id)

    characters = {item["id"]: item for item in season.get("characters", []) if isinstance(item, dict) and "id" in item}
    locations = {item["id"] for item in season.get("locations", []) if isinstance(item, dict) and "id" in item}
    props = {item["id"] for item in season.get("props", []) if isinstance(item, dict) and "id" in item}
    episode_id = episode.get("id", "")
    if not isinstance(episode_id, str) or not _ID.fullmatch(episode_id):
        issue("episode_id", "Episode id must be a nonempty ASCII identifier.")
    if not episode.get("title"):
        issue("episode_title", "Episode title is missing.")
    shots = episode.get("shots")
    if not isinstance(shots, list) or not shots:
        issue("missing_shots", "Episode must contain shots.")
        return issues
    if not 12 <= len(shots) <= 16:
        issue("shot_count", f"Episode has {len(shots)} shots; the writing target is 12–16.", severity="warning")

    seen: set[str] = set()
    previous: dict | None = None
    for index, shot in enumerate(shots):
        if not isinstance(shot, dict):
            issue("shot_type", f"Shot {index + 1} is not an object.")
            continue
        shot_id = shot.get("id", "")
        if not isinstance(shot_id, str) or not _ID.fullmatch(shot_id):
            issue("shot_id", "Shot id must be a nonempty ASCII identifier.", str(shot_id))
            shot_id = str(shot_id)
        if shot_id in seen:
            issue("duplicate_shot_id", "Shot id is duplicated in this episode.", shot_id)
        seen.add(shot_id)
        if not shot.get("scene_id"):
            issue("scene_id", "A scene_id is required to track continuous scenes.", shot_id)
        if shot.get("location_id") not in locations:
            issue("unknown_location", f"Unknown location_id: {shot.get('location_id')!r}.", shot_id)
        if not shot.get("time_of_day"):
            issue("time_of_day", "Time of day is missing.", shot_id)

        duration = shot.get("duration")
        valid_duration = isinstance(duration, (int, float)) and not isinstance(duration, bool) and math.isfinite(duration) and duration > 0
        if not valid_duration:
            issue("duration", "Duration must be a positive finite number.", shot_id)
        else:
            if duration > 6:
                issue("duration_limit", f"Planned duration {duration:g}s exceeds the 6s limit.", shot_id)
            elif duration < 4:
                issue("short_duration", f"Planned duration is only {duration:g}s; check the action and dialogue budget.", shot_id, "warning")
            rendered_duration = h3_frame_count(duration) / FPS
            if rendered_duration > 6:
                issue("h3_duration_limit", f"H3 rounds {duration:g}s to {rendered_duration:.3f}s; use 5.875s or less.", shot_id)

        cast = shot.get("characters", [])
        if not isinstance(cast, list):
            issue("cast_type", "characters must be a list.", shot_id)
            cast = []
        cast_ids: set[str] = set()
        for member in cast:
            if not isinstance(member, dict):
                issue("cast_type", "Each character reference must be an object.", shot_id)
                continue
            character_id = member.get("id")
            if character_id in cast_ids:
                issue("duplicate_character", f"Character {character_id!r} occurs twice in the shot cast.", shot_id)
            cast_ids.add(str(character_id))
            character = characters.get(character_id)
            if character is None:
                issue("unknown_character", f"Unknown character: {character_id!r}.", shot_id)
                continue
            wardrobes = {item.get("id") for item in character.get("wardrobes", []) if isinstance(item, dict)}
            if member.get("wardrobe_id") not in wardrobes:
                issue("unknown_wardrobe", f"Unknown wardrobe {member.get('wardrobe_id')!r} for {character_id}.", shot_id)
        prop_ids = shot.get("prop_ids", [])
        if not isinstance(prop_ids, list):
            issue("props_type", "prop_ids must be a list.", shot_id)
        else:
            for prop_id in prop_ids:
                if prop_id not in props:
                    issue("unknown_prop", f"Unknown prop_id: {prop_id!r}.", shot_id)

        for field in ("visual_en", "camera_en", "soundscape_en", "first_frame_prompt_en"):
            english(shot.get(field), field, shot_id)
        acceptance = shot.get("acceptance_en", [])
        if not isinstance(acceptance, list) or not acceptance:
            issue("acceptance", "At least one observable English acceptance criterion is required.", shot_id)
        else:
            for criterion in acceptance:
                english(criterion, "acceptance_en", shot_id)
        for field in ("beat_zh", "action_start_zh", "action_end_zh", "cut_reason_zh"):
            if not isinstance(shot.get(field), str) or not shot[field].strip():
                issue("missing_story_field", f"{field} is missing.", shot_id)
        if shot.get("action_complete") is not True:
            issue("unfinished_action", "The shot must complete its central action before the cut.", shot_id)

        dialogue = shot.get("dialogue", [])
        if not isinstance(dialogue, list):
            issue("dialogue_type", "dialogue must be a list.", shot_id)
            dialogue = []
        if len(dialogue) > 1:
            issue("dialogue_count", "Use at most one complete spoken line per short shot.", shot_id)
        speakers: set[str] = set()
        total_characters = 0
        for line in dialogue:
            if not isinstance(line, dict):
                issue("dialogue_type", "Each dialogue line must be an object.", shot_id)
                continue
            speaker = line.get("speaker_id")
            if not isinstance(line.get("voiceover"), bool):
                issue("voiceover_type", "voiceover must be a JSON boolean.", shot_id)
            speakers.add(str(speaker))
            if speaker not in characters:
                issue("unknown_speaker", f"Unknown speaker_id: {speaker!r}.", shot_id)
            if line.get("voiceover") is not True and speaker not in cast_ids:
                issue("speaker_not_visible", f"On-screen speaker {speaker!r} is missing from the shot cast.", shot_id)
            english(line.get("delivery_en"), "dialogue.delivery_en", shot_id)
            text = line.get("text_zh", "")
            if not isinstance(text, str) or not text.strip():
                issue("empty_dialogue", "Dialogue text is empty.", shot_id)
                continue
            if "<" in text or ">" in text:
                issue("dialogue_markup", "text_zh must be plain spoken text without markup.", shot_id)
            if not _CHINESE.search(text):
                issue("dialogue_language", "Dialogue must be Mandarin Chinese text.", shot_id)
            total_characters += spoken_length(text)
        if len(speakers) > 1:
            issue("multiple_speakers", "Use at most one speaker in a shot lasting six seconds or less.", shot_id)
        if total_characters > 22:
            issue("dialogue_length", f"Shot has {total_characters} spoken characters; the hard limit is 22.", shot_id)
        elif total_characters > 18:
            issue("long_dialogue", f"Shot has {total_characters} spoken characters; prefer at most 18.", shot_id, "warning")
        if total_characters and valid_duration and total_characters / 4 + 0.8 > duration:
            issue("dialogue_timing", f"{total_characters} characters need about {total_characters / 4 + 0.8:.2f}s including a short pause; shot allows {duration:g}s.", shot_id)

        if previous is not None:
            previous_cast = {item.get("id") for item in previous.get("characters", []) if isinstance(item, dict)}
            same_scene = previous.get("scene_id") == shot.get("scene_id") and previous.get("location_id") == shot.get("location_id")
            same_people = bool(cast_ids) and previous_cast == cast_ids
            if same_scene and same_people:
                previous_end = str(previous.get("action_end_zh", ""))
                current_start = str(shot.get("action_start_zh", ""))
                if previous.get("action_complete") is not True or _UNFINISHED.search(previous_end) or _CONTINUED.search(current_start):
                    issue("adjacent_action_continuation", f"Same cast and scene as {previous.get('id')}; an unfinished action appears to continue across the cut. Merge it or redesign the cut.", shot_id, "warning")
        previous = shot
    return issues
