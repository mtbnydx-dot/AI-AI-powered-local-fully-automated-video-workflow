import base64
import io
import json
from pathlib import Path
from typing import Literal

import httpx
from PIL import Image, ImageOps
from pydantic import BaseModel, ConfigDict, Field

from videopipe.media import inspect_video, sample_frames
from videopipe.models import ReviewConfig, ReviewIssue, ReviewResult, Shot
from videopipe.prompts import load_prompt


class VLLMReviewer:
    def __init__(self, config: ReviewConfig, script: str = ""):
        self.config = config
        self.script = script

    def review(
        self,
        video: Path,
        *,
        shots: list[Shot],
        scope: Literal["shot", "film"],
        output_dir: Path,
    ) -> ReviewResult:
        output_dir.mkdir(parents=True, exist_ok=True)
        if scope != "shot":
            return _save_result(output_dir, ReviewResult(
                passed=False, available=False, summary="Whole-film review is a human decision; automatic visual review is shot-only."
            ))
        try:
            info = inspect_video(video)
            frames = sample_frames(
                video, output_dir / "frames", self.config.sample_frames, self.config.max_frame_size
            )
            request = self._build_request(frames, info.duration, shots)
            result = _parse_response(self._chat(request, output_dir), shots)
        except (httpx.HTTPError, ValueError, RuntimeError, OSError) as error:
            (output_dir / "error.txt").write_text(str(error), encoding="utf-8")
            result = ReviewResult(passed=False, available=False, summary=f"Visual review unavailable: {error}")
        return _save_result(output_dir, result)

    def _chat(self, request: dict, output_dir: Path) -> dict:
        """Each call owns its HTTP client and output directory; workers may call concurrently."""
        _write_json(output_dir / "request.json", request)
        with httpx.Client(timeout=self.config.timeout, trust_env=False) as client:
            response = client.post(self.config.url.rstrip("/") + "/chat/completions", json=request)
        (output_dir / "response.txt").write_text(response.text, encoding="utf-8")
        response.raise_for_status()
        return response.json()

    def _build_request(self, frames: list[Path], duration: float, shots: list[Shot]) -> dict:
        shot_specs = [
            {
                "id": shot.id,
                "prompt": shot.prompt,
                "negative_prompt": shot.negative_prompt,
                "planned_duration_seconds": shot.duration,
                "criteria": shot.acceptance,
            }
            for shot in shots
        ]
        content = [{
            "type": "text",
            "text": load_prompt(
                "review_user.txt", duration=duration, script=self.script,
                shot_specs=json.dumps(shot_specs, ensure_ascii=False),
            ),
        }]
        for shot in shots:
            for index, reference in enumerate(shot.reference_images, start=1):
                content.append({"type": "text", "text": load_prompt(
                    "review_reference.txt", index=index, shot_id=shot.id,
                )})
                content.append(_image_part(reference, self.config.max_frame_size))
        for index, frame in enumerate(frames):
            timestamp = duration * (index + 0.5) / len(frames)
            content.append({"type": "text", "text": load_prompt("review_frame.txt", timestamp=timestamp)})
            content.append(_image_part(frame, self.config.max_frame_size))
        return {
            "model": self.config.model,
            "temperature": 0,
            "max_tokens": self.config.max_tokens,
            "chat_template_kwargs": {"enable_thinking": False},
            "messages": [
                {
                    "role": "system",
                    "content": load_prompt("review_system.txt"),
                },
                {"role": "user", "content": content},
            ],
            "structured_outputs": {"json": ReviewResult.model_json_schema()},
        }

    def compare_transcript(self, script_text: str, transcript: dict, output_dir: Path) -> dict:
        """A text-only advisory record. This result never approves or rejects generated media."""
        output_dir.mkdir(parents=True, exist_ok=True)
        if transcript.get("status") != "complete":
            result = {"available": False, "matches": None, "summary": "Transcript unavailable; script comparison skipped.",
                      "differences": []}
            _write_json(output_dir / "result.json", result)
            return result
        request = {
            "model": self.config.model, "temperature": 0, "max_tokens": self.config.max_tokens,
            "chat_template_kwargs": {"enable_thinking": False},
            "messages": [
                {"role": "system", "content": load_prompt("transcript_compare_system.txt")},
                {"role": "user", "content": json.dumps({"script": script_text,
                    "segments": transcript.get("segments", [])}, ensure_ascii=False)},
            ],
            "structured_outputs": {"json": _TranscriptComparison.model_json_schema()},
        }
        try:
            decision = _TranscriptComparison.model_validate_json(
                _completion_content(self._chat(request, output_dir)), strict=True
            )
            result = {"available": True, **decision.model_dump()}
        except (httpx.HTTPError, ValueError, RuntimeError, OSError) as error:
            (output_dir / "error.txt").write_text(str(error), encoding="utf-8")
            result = {"available": False, "matches": None, "summary": f"Script comparison unavailable: {error}",
                      "differences": []}
        _write_json(output_dir / "result.json", result)
        return result


class CPUReviewer:
    def __init__(self, config: ReviewConfig, script: str = ""):
        self.config = config
        self.script = script

    def review(
        self,
        video: Path,
        *,
        shots: list[Shot],
        scope: Literal["shot", "film"],
        output_dir: Path,
    ) -> ReviewResult:
        output_dir.mkdir(parents=True, exist_ok=True)
        if scope != "shot":
            return _save_result(output_dir, ReviewResult(
                passed=False, available=False, summary="Whole-film review is a human decision; CPU review is shot-only."
            ))
        info = inspect_video(video)
        frames = sample_frames(
            video, output_dir / "frames", self.config.sample_frames, self.config.max_frame_size
        )
        _write_json(output_dir / "request.json", {
            "backend": "cpu", "scope": scope, "video": str(video),
            "duration_seconds": info.duration, "sampled_frames": len(frames),
            "shot_ids": [shot.id for shot in shots],
        })
        # Persisted attempt directories make this explicit test exercise reproducible on resume.
        injected = output_dir.parent.name == "attempt-001" and shots[0].id in self.config.reject_once
        issues = [ReviewIssue(
            shot_id=shots[0].id, category="generation", timestamps=[0.0],
            message="Explicit CPU reject_once test exercise; this is not a semantic quality judgement.",
        )] if injected else []
        return _save_result(output_dir, ReviewResult(
            passed=not injected,
            summary="CPU technical check only: video metadata and sampled frame decoding succeeded.",
            warnings=["Semantic quality and motion were not assessed."], issues=issues,
        ))

    def compare_transcript(self, script_text: str, transcript: dict, output_dir: Path) -> dict:
        output_dir.mkdir(parents=True, exist_ok=True)
        result = {"available": False, "matches": None, "summary": "No language model configured for script comparison.",
                  "differences": []}
        _write_json(output_dir / "result.json", result)
        return result


class _TranscriptComparison(BaseModel):
    model_config = ConfigDict(extra="forbid")
    matches: bool
    summary: str
    differences: list[str] = Field(default_factory=list)


def _image_part(path: Path, max_size: int) -> dict:
    with Image.open(path) as source:
        image = ImageOps.exif_transpose(source).convert("RGB")
        image.thumbnail((max_size, max_size))
        buffer = io.BytesIO()
        image.save(buffer, format="JPEG", quality=90)
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{encoded}"}}


def _completion_content(payload: dict) -> str:
    try:
        choice = payload["choices"][0]
        if choice["finish_reason"] != "stop":
            raise ValueError(f"Review did not finish normally: {choice['finish_reason']}")
        return choice["message"]["content"]
    except (KeyError, IndexError, TypeError) as error:
        raise ValueError("Invalid vLLM response: missing completion content") from error


def _parse_response(payload: dict, shots: list[Shot]) -> ReviewResult:
    # Validate the service contract once at this boundary. Unlocated observations are warnings.
    result = ReviewResult.model_validate_json(_completion_content(payload), strict=True)
    if not result.available:
        return result.model_copy(update={"passed": False})
    if result.passed and result.issues:
        raise ValueError("Invalid review result: passed=true with unresolved issues")
    shot_ids = {shot.id for shot in shots}
    issues, warnings = [], list(result.warnings)
    for issue in result.issues:
        if issue.shot_id not in shot_ids:
            raise ValueError(f"Invalid review result: unknown shot_id {issue.shot_id!r}")
        if not issue.message.strip():
            raise ValueError("Invalid review result: an issue needs observed evidence")
        if issue.category != "generation" or not issue.timestamps:
            warnings.append(issue.message)
        else:
            issues.append(issue)
    if not result.passed and not issues and not warnings:
        warnings.append(result.summary or "Reviewer could not identify a substantial visible problem.")
    return result.model_copy(update={"passed": not issues, "issues": issues, "warnings": warnings})


def _save_result(output_dir: Path, result: ReviewResult) -> ReviewResult:
    _write_json(output_dir / "result.json", result.model_dump(mode="json"))
    return result


def _write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
