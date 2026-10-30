"""Conservative alignment of original prompt dialogue to observed ASR timing."""

from __future__ import annotations

from pathlib import Path
import re
import unicodedata

from videopipe.models import Shot
from videopipe.records import write_json


def extract_dialogue(shots: list[Shot]) -> list[dict]:
    """Read dialogue only; never infer spoken words from scene descriptions."""
    dialogue = []
    offset = 0.0
    for shot in shots:
        for index, match in enumerate(re.finditer(r"<d>(.*?)</d>", shot.prompt, re.DOTALL), start=1):
            content = match.group(1).strip()
            language_match = re.match(r"^\[([^\]]+)\]\s*", content)
            language = language_match.group(1) if language_match else None
            text = content[language_match.end():] if language_match else content
            # These H3 continuity markers are instructions, not spoken words.
            text = re.sub(r"</?(?:scenetrans|cutoff)>", "", text).strip()
            if text:
                dialogue.append({"shot_id": shot.id, "dialogue_index": index,
                                 "language": language, "text": text,
                                 "shot_start": offset, "shot_end": offset + shot.duration})
        offset += shot.duration
    return dialogue


def create_subtitles(shots: list[Shot], transcript: dict, output_dir: Path) -> dict:
    """Write only reliable matches, recording omissions instead of guessing times."""
    dialogue = extract_dialogue(shots)
    result = {"status": "unavailable", "path": None, "matched": [], "unmatched": []}
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
        path = output_dir / "episode.srt"
        path.unlink(missing_ok=True)
        if not dialogue:
            result["status"] = "no_dialogue"
        elif transcript.get("status") != "complete":
            result["unmatched"] = [{**line, "reason": "asr_unavailable"} for line in dialogue]
        else:
            units = _timestamped_units(transcript.get("segments", []))
            cursor = 0.0
            for line in dialogue:
                match = _find_match(line, units, cursor)
                if match is None:
                    result["unmatched"].append({**line, "reason": "no_reliable_timestamp_match"})
                    continue
                start, end = match
                result["matched"].append({**line, "start": start, "end": end})
                cursor = end
            if result["matched"]:
                cues = [f"{index}\n{_srt_time(line['start'])} --> {_srt_time(line['end'])}\n{line['text']}\n"
                        for index, line in enumerate(result["matched"], start=1)]
                path.write_text("\n".join(cues), encoding="utf-8-sig")
                result.update(path=str(path), status="partial" if result["unmatched"] else "complete")
            else:
                result["status"] = "unaligned"
    except Exception as error:
        result.update(status="unavailable", path=None, error=f"{type(error).__name__}: {error}")
    try:
        write_json(output_dir / "subtitles.json", result)
    except OSError as error:
        result["record_error"] = str(error)
    return result


def _normalized(text: str) -> tuple[str, ...]:
    text = unicodedata.normalize("NFKC", text).casefold()
    # Chinese ASR word boundaries vary; compare ideographs individually while
    # preserving word boundaries in alphabetic languages ("a part" != "apart").
    cjk = "\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff"
    return tuple(re.findall(f"[{cjk}]|[^\\W_{cjk}]+", text))


def _timestamped_units(segments: list[dict]) -> list[dict]:
    # ASR validates text and timestamp fields once at its subprocess boundary.
    units = []
    for segment in segments:
        items = segment.get("words") or [segment]
        for item in items:
            text = item.get("word", item.get("text", ""))
            normalized = _normalized(text)
            if not normalized:
                continue
            units.append({"start": item["start"], "end": item["end"],
                          "text": normalized})
    return sorted(units, key=lambda item: (item["start"], item["end"]))


def _find_match(line: dict, units: list[dict], cursor: float) -> tuple[float, float] | None:
    target = _normalized(line["text"])
    if not target or re.search(r"\n\s*\n", line["text"]):
        return None
    # Use only observed intervals within this shot. Never split a segment by
    # character count or invent timestamps for missing dialogue.
    eligible = [item for item in units if item["start"] >= max(line["shot_start"], cursor)
                and item["end"] <= line["shot_end"]]
    for index, first in enumerate(eligible):
        combined = ()
        previous_end = first["start"]
        for item in eligible[index:]:
            if item["start"] < previous_end:
                break
            combined += item["text"]
            previous_end = item["end"]
            if combined == target:
                return first["start"], item["end"]
            if target[:len(combined)] != combined:
                break
    return None


def _srt_time(seconds: float) -> str:
    milliseconds = round(seconds * 1000)
    hours, milliseconds = divmod(milliseconds, 3_600_000)
    minutes, milliseconds = divmod(milliseconds, 60_000)
    seconds, milliseconds = divmod(milliseconds, 1000)
    return f"{hours:02}:{minutes:02}:{seconds:02},{milliseconds:03}"
