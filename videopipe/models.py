from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, ConfigDict


class InputModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Shot(InputModel):
    id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
    prompt: str = Field(min_length=1)
    negative_prompt: str = ""
    reference_images: list[Path] = Field(default_factory=list)
    duration: float = Field(default=3, gt=0)
    width: int = Field(default=864, gt=0, multiple_of=2)
    height: int = Field(default=480, gt=0, multiple_of=2)
    fps: int = Field(default=24, gt=0)
    seed: int = Field(default=42, ge=0)
    parameters: dict[str, Any] = Field(default_factory=dict)
    acceptance: list[str] = Field(default_factory=list)


class ScriptPackage(InputModel):
    title: str = Field(min_length=1)
    script: Path
    shots: list[Shot] = Field(min_length=1)


class ComfyConfig(InputModel):
    urls: list[str] = Field(default_factory=lambda: ["http://127.0.0.1:8188"], min_length=1)
    workflow: Path | None = None
    upscale_workflow: Path | None = None
    parameters: dict[str, Any] = Field(default_factory=dict)
    frame_multiple: int = Field(default=17, gt=0)
    frame_offset: int = Field(default=5, ge=0)
    poll_interval: float = Field(default=2, gt=0)
    timeout: float = Field(default=3600, gt=0)


class ReviewConfig(InputModel):
    backend: Literal["cpu", "vllm"] = "cpu"
    url: str = "http://127.0.0.1:8000/v1"
    model: str = "qwen-review"
    sample_frames: int = Field(default=8, ge=2)
    max_frame_size: int = Field(default=640, ge=64)
    max_tokens: int = Field(default=2048, gt=0)
    timeout: float = Field(default=300, gt=0)
    reject_once: list[str] = Field(default_factory=list)


class MediaConfig(InputModel):
    width: int = Field(default=864, gt=0, multiple_of=2)
    height: int = Field(default=480, gt=0, multiple_of=2)
    fps: int = Field(default=24, gt=0)
    upscale: Literal["none", "lanczos", "comfyui"] = "none"
    upscale_factor: int = Field(default=2, ge=1, le=4)
    crf: int = Field(default=18, ge=0, le=51)


class PipelineConfig(InputModel):
    generator: Literal["cpu", "comfyui"] = "cpu"
    comfyui: ComfyConfig = Field(default_factory=ComfyConfig)
    review: ReviewConfig = Field(default_factory=ReviewConfig)
    media: MediaConfig = Field(default_factory=MediaConfig)
    max_attempts: int = Field(default=3, ge=1)
    max_film_reviews: int = Field(default=3, ge=1)
    workers: int = Field(default=1, ge=1)
    human_approval: bool = True


class ReviewIssue(InputModel):
    shot_id: str | None = None
    category: Literal["generation", "editing"]
    message: str
    suggested_prompt: str | None = None


class ReviewResult(InputModel):
    passed: bool
    summary: str
    issues: list[ReviewIssue] = Field(default_factory=list)

