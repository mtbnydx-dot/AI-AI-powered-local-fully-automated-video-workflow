"""Always deliver script subtitles, refining their timing with usable ASR."""

from __future__ import annotations

from pathlib import Path
from itertools import groupby
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
    """ASR supplies timing, not permission to include a scripted line."""
    dialogue = extract_dialogue(shots)
    units = _timestamped_units(transcript.get("segments", [])) if transcript.get("status") == "complete" else []
    lines = []
    for _, group in groupby(dialogue, key=lambda line: line["shot_id"]):
        lines.extend(_time_shot(list(group), units))
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "episode.srt"
    cues = [f"{index}\n{_srt_time(line['start'])} --> {_srt_time(line['end'])}\n{line['text']}\n"
            for index, line in enumerate(lines, start=1)]
    path.write_text("\n".join(cues), encoding="utf-8")
    matched = [line for line in lines if line["timing"] == "asr"]
    fallback = [line for line in lines if line["timing"] == "script"]
    result = {"status": "complete" if lines else "no_dialogue", "path": str(path),
              "timing": ("mixed" if matched else "script") if fallback else ("asr" if matched else "none"),
              "matched": matched, "fallback": fallback}
    write_json(output_dir / "subtitles.json", result)
    return result


def _time_shot(lines: list[dict], units: list[dict]) -> list[dict]:
    timed, cursor = [], lines[0]["shot_start"]
    for line in lines:
        match = _find_match(line, units, cursor)
        if match:
            start, end = match
            timed.append({**line, "start": start, "end": end, "timing": "asr"})
            cursor = end
        else:
            timed.append(dict(line))
    anchors = [-1, *(i for i, line in enumerate(timed) if "start" in line), len(timed)]
    for left, right in zip(anchors, anchors[1:]):
        if right == left + 1:
            continue
        start = timed[left]["end"] if left >= 0 else lines[0]["shot_start"]
        end = timed[right]["start"] if right < len(timed) else lines[-1]["shot_end"]
        if end - start < 0.001 * (right - left - 1):
            # If an observed line fills the shot, use one coherent coarse timeline.
            return _script_times(lines, lines[0]["shot_start"], lines[-1]["shot_end"])
        timed[left + 1:right] = _script_times(lines[left + 1:right], start, end)
    return timed


def _script_times(lines: list[dict], start: float, end: float) -> list[dict]:
    weights = [max(1, len(_normalized(line["text"]))) for line in lines]
    total, elapsed, timed = sum(weights), 0, []
    for line, weight in zip(lines, weights):
        timed.append({**line, "start": start + (end - start) * elapsed / total,
                      "end": start + (end - start) * (elapsed + weight) / total, "timing": "script"})
        elapsed += weight
    return timed


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
    eligible = [item for item in units if item["end"] > max(line["shot_start"], cursor)
                and item["start"] < line["shot_end"]]
    for index, first in enumerate(eligible):
        combined = ()
        previous_end = first["start"]
        for item in eligible[index:]:
            if item["start"] < previous_end:
                break
            combined += item["text"]
            previous_end = item["end"]
            if combined == target:
                return max(first["start"], line["shot_start"], cursor), min(item["end"], line["shot_end"])
            if target[:len(combined)] != combined:
                break
    return None


def _srt_time(seconds: float) -> str:
    milliseconds = round(seconds * 1000)
    hours, milliseconds = divmod(milliseconds, 3_600_000)
    minutes, milliseconds = divmod(milliseconds, 60_000)
    seconds, milliseconds = divmod(milliseconds, 1000)
    return f"{hours:02}:{minutes:02}:{seconds:02},{milliseconds:03}"
