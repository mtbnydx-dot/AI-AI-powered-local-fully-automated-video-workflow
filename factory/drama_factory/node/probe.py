"""L0 measurements with ffprobe/ffmpeg, taken on the node right after generation (CPU only).

The node measures; the control plane judges with the package's thresholds (policy/review.py).
"""
import json
import re
import subprocess
from pathlib import Path

BLACK = re.compile(r"black_start:\s*([\d.]+)\s+black_end:\s*([\d.]+)\s+black_duration:\s*([\d.]+)")
FREEZE_START = re.compile(r"freeze_start:\s*([\d.]+)")
FREEZE_DURATION = re.compile(r"freeze_duration:\s*([\d.]+)")
SILENCE = re.compile(r"silence_duration:\s*([\d.]+)")
SILENCE_START = re.compile(r"silence_start:\s*(-?[\d.]+)")
MAX_VOLUME = re.compile(r"max_volume:\s*(-?[\d.]+|-inf) dB")
MEAN_VOLUME = re.compile(r"mean_volume:\s*(-?[\d.]+|-inf) dB")
DECODE_ERROR = re.compile(r"error while decoding|corrupt|invalid data found", re.I)


def _rate(value: str | None) -> float:
    if not value or "/" not in value:
        return float(value or 0)
    num, den = value.split("/", 1)
    return float(num) / float(den) if float(den) else 0.0


def _db(match) -> float | None:
    if not match:
        return None
    return -120.0 if match.group(1) == "-inf" else float(match.group(1))


def parse_ffprobe(data: dict) -> dict:
    streams = data.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if video is None:
        return {"decodable": False, "error": "没有视频流"}
    fps = _rate(video.get("avg_frame_rate")) or _rate(video.get("r_frame_rate"))
    duration = float(data.get("format", {}).get("duration") or video.get("duration") or 0)
    return {"decodable": True, "width": video.get("width"), "height": video.get("height"), "fps": round(fps, 3),
            "duration_s": round(duration, 3), "codec": video.get("codec_name"), "has_audio": audio is not None,
            "audio_codec": audio.get("codec_name") if audio else None}


def parse_filters(log: str, duration: float = 0.0) -> dict:
    black = [(float(a), float(b)) for a, b, _ in BLACK.findall(log)]
    freeze_starts = [float(x) for x in FREEZE_START.findall(log)]
    freeze_lengths = [float(x) for x in FREEZE_DURATION.findall(log)]
    if len(freeze_starts) > len(freeze_lengths) and duration:  # freeze still running at end of file
        freeze_lengths.append(max(0.0, duration - freeze_starts[-1]))
    freezes = [[start, round(start + length, 3)] for start, length in zip(freeze_starts, freeze_lengths)]
    silence_starts = [float(x) for x in SILENCE_START.findall(log)]
    silences = [float(x) for x in SILENCE.findall(log)]
    if len(silence_starts) > len(silences) and duration:
        silences.append(max(0.0, duration - silence_starts[-1]))
    return {"black_s": round(sum(b - a for a, b in black), 3), "black_ranges": [list(r) for r in black],
            "freeze_ranges": freezes, "longest_freeze_s": round(max(freeze_lengths, default=0.0), 3),
            "silence_s": round(sum(silences), 3),
            "max_volume_db": _db(MAX_VOLUME.search(log)), "mean_volume_db": _db(MEAN_VOLUME.search(log)),
            "decode_errors": len(DECODE_ERROR.findall(log))}


def probe_video(path: Path, ffprobe: str = "ffprobe", ffmpeg: str = "ffmpeg", timeout: float = 300) -> dict:
    try:
        meta = subprocess.run([ffprobe, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
                              capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"decodable": False, "error": f"ffprobe 失败：{exc}"}
    if meta.returncode != 0:
        return {"decodable": False, "error": meta.stderr.strip()[:500]}
    result = parse_ffprobe(json.loads(meta.stdout or "{}"))
    if not result["decodable"]:
        return result
    command = [ffmpeg, "-hide_banner", "-nostats", "-i", str(path),
               "-vf", "blackdetect=d=0.2:pix_th=0.10,freezedetect=n=-60dB:d=0.75"]
    if result["has_audio"]:
        command += ["-af", "volumedetect,silencedetect=n=-45dB:d=0.5"]
    command += ["-f", "null", "-"]
    try:
        scan = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {**result, "decodable": False, "error": f"全片解码失败：{exc}"}
    result.update(parse_filters(scan.stderr, result["duration_s"]))
    if scan.returncode != 0 or result["decode_errors"]:
        result.update(decodable=False, error=scan.stderr.strip()[-500:])
    return result
