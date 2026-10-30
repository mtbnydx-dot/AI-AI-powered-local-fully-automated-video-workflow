"""Optional, one-video ASR in a separate process on the shared review GPU."""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import math
import os
from pathlib import Path
import subprocess
import sys
from typing import TYPE_CHECKING

from videopipe.records import write_json

if TYPE_CHECKING:
    from videopipe.models import ASRConfig


def transcribe(video: Path, config: ASRConfig, output_dir: Path) -> dict:
    """Return an informational result; missing ASR must never block delivery."""
    result = {"status": "disabled", "model": config.model, "segments": []}
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
        if config.enabled:
            worker_output = (output_dir / "worker-result.json").resolve()
            worker_output.unlink(missing_ok=True)
            command = [
                sys.executable, "-m", "videopipe.backends.asr", str(video.resolve()),
                "--model", config.model, "--compute-type", config.compute_type,
                "--output", str(worker_output),
            ]
            if config.language:
                command.extend(["--language", config.language])
            # Visibility selects a device; it does not impose a VRAM limit.
            environment = {**os.environ, "CUDA_VISIBLE_DEVICES": str(config.gpu),
                           "HF_HUB_OFFLINE": "1"}
            completed = subprocess.run(
                command, env=environment, capture_output=True, text=True,
                timeout=config.timeout, check=False,
            )
            (output_dir / "stdout.log").write_text(completed.stdout, encoding="utf-8")
            (output_dir / "stderr.log").write_text(completed.stderr, encoding="utf-8")
            if completed.returncode:
                raise RuntimeError(
                    f"ASR process exited {completed.returncode}: {completed.stderr[-2000:].strip()}"
                )
            result = json.loads(worker_output.read_text(encoding="utf-8"))
            _validate_result(result)
            result.update(model=config.model, gpu=config.gpu, compute_type=config.compute_type)
    except Exception as error:
        result = {"status": "unavailable", "model": config.model,
                  "segments": [], "error": f"{type(error).__name__}: {error}"}
    try:
        write_json(output_dir / "transcript.json", result)
    except OSError as error:
        result["record_error"] = str(error)
    return result


def _validate_result(result: dict) -> None:
    if not isinstance(result, dict) or result.get("status") != "complete":
        raise ValueError("ASR did not return a complete result")
    if not isinstance(result.get("segments"), list):
        raise ValueError("ASR segments must be a list")
    for segment in result["segments"]:
        if not isinstance(segment, dict) or not isinstance(segment.get("text"), str):
            raise ValueError("ASR segment must include text")
        _validate_times(segment)
        words = segment.get("words", [])
        if not isinstance(words, list):
            raise ValueError("ASR words must be a list")
        for word in words:
            if not isinstance(word, dict) or not isinstance(word.get("word"), str):
                raise ValueError("ASR word must include text")
            _validate_times(word)


def _validate_times(item: dict) -> None:
    start, end = item.get("start"), item.get("end")
    if not all(isinstance(value, (int, float)) and not isinstance(value, bool)
               and math.isfinite(value) for value in (start, end)) or not 0 <= start < end:
        raise ValueError("ASR has invalid timestamps")


def _transcribe_local(video: Path, model_name: str, compute_type: str,
                      language: str | None) -> dict:
    # Import only inside the child; the normal CLI does not require ASR packages.
    from faster_whisper import WhisperModel

    model = WhisperModel(
        model_name, device="cuda", device_index=0, compute_type=compute_type,
        num_workers=1, local_files_only=True,
    )
    segments, info = model.transcribe(
        str(video), language=language, task="transcribe", beam_size=1,
        word_timestamps=True, vad_filter=True, condition_on_previous_text=False,
    )
    records = []
    # WhisperModel is the single-input path, not BatchedInferencePipeline.
    for segment in segments:
        record = {"start": segment.start, "end": segment.end, "text": segment.text}
        if segment.words:
            record["words"] = [
                {"start": word.start, "end": word.end, "word": word.word,
                 "probability": word.probability} for word in segment.words
            ]
        records.append(record)
    versions = {}
    for package in ("faster-whisper", "ctranslate2"):
        try:
            versions[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            pass
    result = {"status": "complete", "model": model_name, "language": info.language,
              "language_probability": info.language_probability, "segments": records,
              "versions": versions}
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Transcribe one video with a locally cached ASR model")
    parser.add_argument("video", type=Path)
    parser.add_argument("--model", required=True)
    parser.add_argument("--compute-type", default="int8_float16")
    parser.add_argument("--language")
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    result = _transcribe_local(
        arguments.video, arguments.model, arguments.compute_type, arguments.language,
    )
    write_json(arguments.output, result)


if __name__ == "__main__":
    main()
