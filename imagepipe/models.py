"""Inputs and execution records shared by the image pipeline."""

from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, Field


class APIConfig(BaseModel):
    base_url: str = "https://api.zlk7301.com"
    env_file: str = ".env"
    key_name: str = "QWEN-IMAGE"
    request_timeout_seconds: float = 90
    max_reference_images: int = Field(default=10, gt=0)


class GenerationConfig(BaseModel):
    model: str = "qwen-image-2.1"
    steps: int = Field(default=20, gt=0)
    width: int = Field(default=720, gt=0)
    height: int = Field(default=1280, gt=0)
    resize_mode: Literal["crop", "pad"] = "crop"
    seed_base: int = 20261101


class SchedulerConfig(BaseModel):
    http_concurrency: int = Field(default=16, gt=0)
    max_active_jobs: int = Field(default=64, gt=0)
    poll_interval_seconds: float = Field(default=5, gt=0)
    max_attempts: int = Field(default=3, gt=0)


class ImageConfig(BaseModel):
    api: APIConfig = Field(default_factory=APIConfig)
    generation: GenerationConfig = Field(default_factory=GenerationConfig)
    scheduler: SchedulerConfig = Field(default_factory=SchedulerConfig)
    continuity: dict[str, Any] = Field(default_factory=dict)


@dataclass
class ImageTask:
    asset_id: str
    mode: Literal["text", "edit", "reference"]
    prompt: str
    reference_ids: list[str]
    target_path: str
    seed: int


@dataclass
class CopyTask:
    asset_id: str
    source_path: str
    target_path: str


@dataclass
class ImagePlan:
    package_root: str
    tasks: list[ImageTask]
    copies: list[CopyTask]
