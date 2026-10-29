import base64
import io
import json
from pathlib import Path
from typing import Literal

import httpx
from PIL import Image, ImageOps

from videopipe.media import inspect_video, sample_frames
from videopipe.models import ReviewConfig, ReviewIssue, ReviewResult, Shot


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
        info = inspect_video(video)
        frames = sample_frames(
            video, output_dir / "frames", self.config.sample_frames, self.config.max_frame_size
        )
        request = self._build_request(frames, info.duration, shots, scope)
        _write_json(output_dir / "request.json", request)
        try:
            with httpx.Client(timeout=self.config.timeout) as client:
                response = client.post(
                    self.config.url.rstrip("/") + "/chat/completions", json=request
                )
            (output_dir / "response.txt").write_text(response.text, encoding="utf-8")
            response.raise_for_status()
            result = _parse_response(response.json(), shots, scope)
        except (httpx.HTTPError, ValueError) as error:
            (output_dir / "error.txt").write_text(str(error), encoding="utf-8")
            raise
        _write_json(output_dir / "result.json", result.model_dump(mode="json"))
        return result

    def _build_request(
        self, frames: list[Path], duration: float, shots: list[Shot], scope: str
    ) -> dict:
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
            "text": (
                f"Review scope: {scope}. Actual video duration: {duration:.3f} seconds.\n"
                f"Script:\n{self.script}\n"
                f"Shots in playback order:\n{json.dumps(shot_specs, ensure_ascii=False)}\n"
                "Reference images follow, then ordered sampled video frames. "
                "Frame timestamps refer to the actual submitted video. "
                "Check requested content, identity, visible defects and continuity. "
                "For film scope also check shot order and editing. "
                "Sparse frames cannot establish audio quality or prove smooth motion; "
                "do not claim to have assessed them."
            ),
        }]
        for shot in shots:
            for index, reference in enumerate(shot.reference_images, start=1):
                content.append({"type": "text", "text": f"Reference {index} for shot {shot.id}:"})
                content.append(_image_part(reference, self.config.max_frame_size))
        for index, frame in enumerate(frames):
            timestamp = duration * (index + 0.5) / len(frames)
            content.append({"type": "text", "text": f"Video frame at {timestamp:.3f}s:"})
            content.append(_image_part(frame, self.config.max_frame_size))
        return {
            "model": self.config.model,
            "temperature": 0,
            "max_tokens": self.config.max_tokens,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You review generated video against its production brief. "
                        "Return only the requested JSON. passed=true requires issues=[]. "
                        "passed=false requires at least one concrete actionable issue. "
                        "Use only supplied shot IDs. A generation issue must identify a shot; "
                        "an editing issue may use shot_id=null for a whole-film problem. "
                        "For a single-shot review all issues must identify that shot. "
                        "Include the observed timestamp and defect in message. "
                        "suggested_prompt may contain a complete replacement prompt when "
                        "regeneration is needed; otherwise use null. "
                        "Report only observed problems, and state assessment limits in summary."
                    ),
                },
                {"role": "user", "content": content},
            ],
            "structured_outputs": {"json": ReviewResult.model_json_schema()},
        }


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
        info = inspect_video(video)
        frames = sample_frames(
            video, output_dir / "frames", self.config.sample_frames, self.config.max_frame_size
        )
        _write_json(output_dir / "request.json", {
            "backend": "cpu",
            "scope": scope,
            "video": str(video),
            "duration_seconds": info.duration,
            "sampled_frames": len(frames),
            "shot_ids": [shot.id for shot in shots],
        })
        # The attempt directory survives process restarts, so a resumed run is not rejected again.
        first_attempt = (
            output_dir.parent.name == "attempt-001" if scope == "shot"
            else output_dir.name == "review-001"
        )
        reject_id = shots[0].id if scope == "shot" else "film"
        injected = first_attempt and reject_id in self.config.reject_once
        issues = [ReviewIssue(
            shot_id=shots[0].id,
            category="generation",
            message="Explicit CPU reject_once exercise; regenerate this shot once.",
        )] if injected else []
        result = ReviewResult(
            passed=not injected,
            summary=(
                "CPU technical check only: video metadata and sampled frame decoding succeeded. "
                "Semantic quality and motion were not assessed."
                + (" Explicit rejection enabled to exercise the repair path." if injected else "")
            ),
            issues=issues,
        )
        _write_json(output_dir / "result.json", result.model_dump(mode="json"))
        return result


def _image_part(path: Path, max_size: int) -> dict:
    with Image.open(path) as source:
        image = ImageOps.exif_transpose(source).convert("RGB")
        image.thumbnail((max_size, max_size))
        buffer = io.BytesIO()
        image.save(buffer, format="JPEG", quality=90)
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{encoded}"}}


def _parse_response(payload: dict, shots: list[Shot], scope: str) -> ReviewResult:
    try:
        choice = payload["choices"][0]
        if choice["finish_reason"] != "stop":
            raise ValueError(f"Review did not finish normally: {choice['finish_reason']}")
        result = ReviewResult.model_validate_json(choice["message"]["content"], strict=True)
    except (KeyError, IndexError, TypeError) as error:
        raise ValueError("Invalid vLLM review response: missing completion content") from error
    if result.passed and result.issues:
        raise ValueError("Invalid review result: passed=true with unresolved issues")
    if not result.passed and not result.issues:
        raise ValueError("Invalid review result: passed=false requires actionable issues")
    shot_ids = {shot.id for shot in shots}
    for issue in result.issues:
        if issue.shot_id is not None and issue.shot_id not in shot_ids:
            raise ValueError(f"Invalid review result: unknown shot_id {issue.shot_id!r}")
        if issue.shot_id is None and (scope == "shot" or issue.category == "generation"):
            raise ValueError("Invalid review result: generation/single-shot issue needs a shot_id")
        if not issue.message.strip():
            raise ValueError("Invalid review result: an issue needs an actionable message")
    return result


def _write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
