from __future__ import annotations

import mimetypes
import os
import platform
import random
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles


APP_DIR = Path(__file__).resolve().parent
WORKSPACE_DIR = APP_DIR.parent
BASE_DIR = Path(os.environ.get("COMFY_BASE_DIR", WORKSPACE_DIR.parent)).expanduser().resolve()
INPUT_DIR = BASE_DIR / "input"
OUTPUT_DIR = BASE_DIR / "output"
TEMP_DIR = BASE_DIR / "temp"
UPLOAD_DIR = INPUT_DIR / "beginner_frontend"
STATIC_DIR = APP_DIR / "static"

COMFY_URL = os.environ.get("COMFY_URL", "http://127.0.0.1:8000").rstrip("/")
CLIENT_ID = f"beginner-frontend-{uuid.uuid4()}"

DEFAULT_IMAGE = "wan22_sample_esports_keyframe.png"
DEFAULT_PROMPT = (
    "a realistic modern esports training room, Queensland esports branding style, "
    "soft RGB lighting, students using gaming peripherals, clean commercial photography, "
    "wide angle lens, natural shadows, cinematic slow dolly-in camera movement, "
    "polished commercial video"
)
DEFAULT_NEGATIVE = (
    "overexposed, low quality, blurry, jpeg artifacts, distorted hands, deformed face, "
    "extra fingers, warped screens, unreadable text, watermark, subtitles, flicker, "
    "jitter, chaotic background, NSFW"
)

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm", ".avi"}

JOBS: dict[str, dict[str, Any]] = {}
INSTALL_JOBS: dict[str, dict[str, Any]] = {}


app = FastAPI(title="Wan2.2 Beginner Frontend")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


def now_ms() -> int:
    return int(time.time() * 1000)


def safe_filename(name: str) -> str:
    suffix = Path(name).suffix.lower()
    stem = Path(name).stem
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", stem).strip("._-") or "upload"
    return f"{stem}_{uuid.uuid4().hex[:8]}{suffix}"


def clamp_int(value: int, low: int, high: int) -> int:
    return max(low, min(high, int(value)))


def clamp_float(value: float, low: float, high: float) -> float:
    return max(low, min(high, float(value)))


def normalize_frames(length: int) -> int:
    length = clamp_int(length, 17, 241)
    # Wan video latents use 1 + 4n frame counts.
    return 1 + 4 * round((length - 1) / 4)


def normalize_dimension(value: int) -> int:
    value = clamp_int(value, 256, 1536)
    return max(32, round(value / 32) * 32)


def normalize_seed(seed: str | int | None) -> int:
    if seed in (None, "", "-1"):
        return random.randint(0, 2**63 - 1)
    try:
        parsed = int(seed)
    except ValueError:
        return random.randint(0, 2**63 - 1)
    if parsed < 0:
        return random.randint(0, 2**63 - 1)
    return parsed


def text_or_default(value: str | None, fallback: str) -> str:
    value = (value or "").strip()
    return value or fallback


def clean_label(value: str | None) -> str:
    value = re.sub(r"\s+", " ", (value or "").strip())
    return value[:80]


def clean_id(value: str | None) -> str:
    return re.sub(r"[^A-Za-z0-9_.:-]+", "", (value or "").strip())[:80]


def workflow_profile_specs() -> dict[str, dict[str, Any]]:
    return {
        "wan22_ti2v_5b_720p": {
            "mode": "ti2v_5b",
            "label": "Wan2.2 TI2V-5B 720P",
        },
        "wan22_ti2v_5b_480p": {
            "mode": "ti2v_5b",
            "label": "Wan2.2 TI2V-5B 480P 小显存",
        },
        "wan22_i2v_a14b_720p": {
            "mode": "i2v_a14b",
            "label": "Wan2.2 I2V-A14B 720P",
        },
        "wan22_i2v_a14b_480p": {
            "mode": "i2v_a14b",
            "label": "Wan2.2 I2V-A14B 480P",
        },
        "wan22_t2v_a14b_720p": {
            "mode": "t2v_a14b",
            "label": "Wan2.2 T2V-A14B 720P",
        },
        "wan22_t2v_a14b_480p": {
            "mode": "t2v_a14b",
            "label": "Wan2.2 T2V-A14B 480P",
        },
        "wan22_ti2v_5b_final_720p": {
            "mode": "ti2v_5b",
            "label": "Wan2.2 TI2V-5B 720P 轻量正片",
        },
        "wan22_ti2v_5b_final_480p": {
            "mode": "ti2v_5b",
            "label": "Wan2.2 TI2V-5B 480P 小显存正片",
        },
        "ffmpeg_deflicker_balanced": {
            "mode": "deflicker",
            "label": "ffmpeg deflicker + hqdn3d",
            "local": True,
        },
        "rife49_2x": {
            "mode": "rife_2x",
            "label": "RIFE 4.9 2x",
            "rife_multiplier": 2,
        },
        "rife49_4x": {
            "mode": "rife_2x",
            "label": "RIFE 4.9 4x",
            "rife_multiplier": 4,
        },
        "realesrgan_x2plus": {
            "mode": "upscale_2x",
            "label": "RealESRGAN x2plus 2x",
            "upscale_model": "RealESRGAN_x2plus.pth",
            "scale": 2,
        },
        "ultrasharp_4x": {
            "mode": "upscale_2x",
            "label": "4x-UltraSharp 4x",
            "upscale_model": "4x-UltraSharp.pth",
            "scale": 4,
        },
    }


def resolve_workflow_profile(
    *,
    model_profile: str | None,
    mode: str,
    model_label: str | None,
    upscale_model: str,
    rife_multiplier: int,
) -> dict[str, Any]:
    profile_id = clean_id(model_profile)
    spec = workflow_profile_specs().get(profile_id, {})
    resolved_mode = spec.get("mode", mode)
    resolved_label = clean_label(model_label) or spec.get("label", "")
    resolved_upscale = spec.get("upscale_model", upscale_model)
    resolved_rife = int(spec.get("rife_multiplier", rife_multiplier))
    return {
        "profile": profile_id,
        "mode": resolved_mode,
        "label": resolved_label,
        "upscale_model": resolved_upscale,
        "rife_multiplier": resolved_rife,
        "scale": spec.get("scale"),
        "local": bool(spec.get("local")),
    }


async def comfy_get(path: str) -> Any:
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(f"{COMFY_URL}{path}")
            response.raise_for_status()
            return response.json()
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"ComfyUI 连接失败：{exc}",
        ) from exc


async def comfy_post(path: str, payload: dict[str, Any]) -> Any:
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(f"{COMFY_URL}{path}", json=payload)
            if response.status_code >= 400:
                try:
                    detail = response.json()
                except ValueError:
                    detail = response.text
                raise HTTPException(status_code=response.status_code, detail=detail)
            return response.json()
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"ComfyUI 连接失败：{exc}",
        ) from exc


async def save_upload(upload: UploadFile, allowed: set[str]) -> str:
    suffix = Path(upload.filename or "").suffix.lower()
    if suffix not in allowed:
        allowed_text = ", ".join(sorted(allowed))
        raise HTTPException(status_code=400, detail=f"文件格式不支持，请使用：{allowed_text}")

    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    filename = safe_filename(upload.filename or f"upload{suffix}")
    destination = UPLOAD_DIR / filename
    with destination.open("wb") as output:
        shutil.copyfileobj(upload.file, output)
    return f"beginner_frontend/{filename}"


def copy_media_to_input(filename: str, subfolder: str, file_type: str) -> str:
    source = safe_media_path(filename, subfolder, file_type)
    if source.suffix.lower() not in VIDEO_EXTENSIONS:
        raise HTTPException(status_code=400, detail="上一阶段输出不是可处理的视频")

    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    destination_name = safe_filename(source.name)
    destination = UPLOAD_DIR / destination_name
    shutil.copy2(source, destination)
    return f"beginner_frontend/{destination_name}"


def node(class_type: str, inputs: dict[str, Any]) -> dict[str, Any]:
    return {"class_type": class_type, "inputs": inputs}


def build_ti2v_prompt(
    *,
    prompt: str,
    negative: str,
    image_name: str,
    width: int,
    height: int,
    length: int,
    fps: int,
    seed: int,
    steps: int,
    cfg: float,
) -> dict[str, Any]:
    return {
        "1": node("LoadImage", {"image": image_name}),
        "2": node(
            "CLIPLoader",
            {
                "clip_name": "umt5_xxl_fp8_e4m3fn_scaled.safetensors",
                "type": "wan",
                "device": "default",
            },
        ),
        "3": node("VAELoader", {"vae_name": "wan2.2_vae.safetensors"}),
        "4": node(
            "UNETLoader",
            {
                "unet_name": "Wan2.2\\wan2.2_ti2v_5B_fp16.safetensors",
                "weight_dtype": "default",
            },
        ),
        "5": node("ModelSamplingSD3", {"model": ["4", 0], "shift": 5.0}),
        "6": node("CLIPTextEncode", {"text": prompt, "clip": ["2", 0]}),
        "7": node("CLIPTextEncode", {"text": negative, "clip": ["2", 0]}),
        "8": node(
            "Wan22ImageToVideoLatent",
            {
                "vae": ["3", 0],
                "width": width,
                "height": height,
                "length": length,
                "batch_size": 1,
                "start_image": ["1", 0],
            },
        ),
        "9": node(
            "KSampler",
            {
                "model": ["5", 0],
                "seed": seed,
                "steps": steps,
                "cfg": cfg,
                "sampler_name": "euler",
                "scheduler": "simple",
                "positive": ["6", 0],
                "negative": ["7", 0],
                "latent_image": ["8", 0],
                "denoise": 1.0,
            },
        ),
        "10": node(
            "VAEDecodeTiled",
            {
                "samples": ["9", 0],
                "vae": ["3", 0],
                "tile_size": 512,
                "overlap": 64,
                "temporal_size": 16,
                "temporal_overlap": 4,
            },
        ),
        "11": node("CreateVideo", {"images": ["10", 0], "fps": fps}),
        "12": node(
            "SaveVideo",
            {
                "video": ["11", 0],
                "filename_prefix": "wan22_frontend/TI2V_draft_%date:yyyy-MM-dd%",
                "format": "mp4",
                "codec": "h264",
            },
        ),
    }


def build_a14b_i2v_prompt(
    *,
    prompt: str,
    negative: str,
    image_name: str,
    width: int,
    height: int,
    length: int,
    fps: int,
    seed: int,
    steps: int,
    cfg: float,
) -> dict[str, Any]:
    split_step = max(1, steps // 2)
    return {
        "10": node(
            "CLIPLoader",
            {
                "clip_name": "umt5_xxl_fp8_e4m3fn_scaled.safetensors",
                "type": "wan",
                "device": "default",
            },
        ),
        "11": node("CLIPTextEncode", {"text": prompt, "clip": ["10", 0]}),
        "12": node("CLIPTextEncode", {"text": negative, "clip": ["10", 0]}),
        "13": node("VAELoader", {"vae_name": "wan_2.1_vae.safetensors"}),
        "14": node("LoadImage", {"image": image_name}),
        "20": node(
            "WanImageToVideo",
            {
                "positive": ["11", 0],
                "negative": ["12", 0],
                "vae": ["13", 0],
                "width": width,
                "height": height,
                "length": length,
                "batch_size": 1,
                "start_image": ["14", 0],
            },
        ),
        "30": node(
            "UNETLoader",
            {
                "unet_name": "Wan2.2\\wan2.2_i2v_high_noise_14B_fp8_scaled.safetensors",
                "weight_dtype": "default",
            },
        ),
        "31": node(
            "LoraLoaderModelOnly",
            {
                "model": ["30", 0],
                "lora_name": "Wan2.2\\wan2.2_i2v_lightx2v_4steps_lora_v1_high_noise.safetensors",
                "strength_model": 1.0,
            },
        ),
        "32": node("ModelSamplingSD3", {"model": ["31", 0], "shift": 5.0}),
        "40": node(
            "UNETLoader",
            {
                "unet_name": "Wan2.2\\wan2.2_i2v_low_noise_14B_fp8_scaled.safetensors",
                "weight_dtype": "default",
            },
        ),
        "41": node(
            "LoraLoaderModelOnly",
            {
                "model": ["40", 0],
                "lora_name": "Wan2.2\\wan2.2_i2v_lightx2v_4steps_lora_v1_low_noise.safetensors",
                "strength_model": 1.0,
            },
        ),
        "42": node("ModelSamplingSD3", {"model": ["41", 0], "shift": 5.0}),
        "50": node(
            "KSamplerAdvanced",
            {
                "model": ["32", 0],
                "add_noise": "enable",
                "noise_seed": seed,
                "steps": steps,
                "cfg": cfg,
                "sampler_name": "euler",
                "scheduler": "simple",
                "positive": ["20", 0],
                "negative": ["20", 1],
                "latent_image": ["20", 2],
                "start_at_step": 0,
                "end_at_step": split_step,
                "return_with_leftover_noise": "enable",
            },
        ),
        "51": node(
            "KSamplerAdvanced",
            {
                "model": ["42", 0],
                "add_noise": "disable",
                "noise_seed": seed,
                "steps": steps,
                "cfg": cfg,
                "sampler_name": "euler",
                "scheduler": "simple",
                "positive": ["20", 0],
                "negative": ["20", 1],
                "latent_image": ["50", 0],
                "start_at_step": split_step,
                "end_at_step": steps,
                "return_with_leftover_noise": "disable",
            },
        ),
        "60": node(
            "VAEDecodeTiled",
            {
                "samples": ["51", 0],
                "vae": ["13", 0],
                "tile_size": 512,
                "overlap": 64,
                "temporal_size": 16,
                "temporal_overlap": 4,
            },
        ),
        "70": node("CreateVideo", {"images": ["60", 0], "fps": fps}),
        "80": node(
            "SaveVideo",
            {
                "video": ["70", 0],
                "filename_prefix": "wan22_frontend/I2V_A14B_%date:yyyy-MM-dd%",
                "format": "mp4",
                "codec": "h264",
            },
        ),
    }


def build_a14b_t2v_prompt(
    *,
    prompt: str,
    negative: str,
    width: int,
    height: int,
    length: int,
    fps: int,
    seed: int,
    steps: int,
    cfg: float,
) -> dict[str, Any]:
    split_step = max(1, steps // 2)
    return {
        "10": node(
            "CLIPLoader",
            {
                "clip_name": "umt5_xxl_fp8_e4m3fn_scaled.safetensors",
                "type": "wan",
                "device": "default",
            },
        ),
        "11": node("CLIPTextEncode", {"text": prompt, "clip": ["10", 0]}),
        "12": node("CLIPTextEncode", {"text": negative, "clip": ["10", 0]}),
        "13": node("VAELoader", {"vae_name": "wan_2.1_vae.safetensors"}),
        "20": node(
            "EmptyHunyuanLatentVideo",
            {"width": width, "height": height, "length": length, "batch_size": 1},
        ),
        "30": node(
            "UNETLoader",
            {
                "unet_name": "Wan2.2\\wan2.2_t2v_high_noise_14B_fp8_scaled.safetensors",
                "weight_dtype": "default",
            },
        ),
        "31": node(
            "LoraLoaderModelOnly",
            {
                "model": ["30", 0],
                "lora_name": "Wan2.2\\wan2.2_t2v_lightx2v_4steps_lora_v1.1_high_noise.safetensors",
                "strength_model": 1.0,
            },
        ),
        "32": node("ModelSamplingSD3", {"model": ["31", 0], "shift": 5.0}),
        "40": node(
            "UNETLoader",
            {
                "unet_name": "Wan2.2\\wan2.2_t2v_low_noise_14B_fp8_scaled.safetensors",
                "weight_dtype": "default",
            },
        ),
        "41": node(
            "LoraLoaderModelOnly",
            {
                "model": ["40", 0],
                "lora_name": "Wan2.2\\wan2.2_t2v_lightx2v_4steps_lora_v1.1_low_noise.safetensors",
                "strength_model": 1.0,
            },
        ),
        "42": node("ModelSamplingSD3", {"model": ["41", 0], "shift": 5.0}),
        "50": node(
            "KSamplerAdvanced",
            {
                "model": ["32", 0],
                "add_noise": "enable",
                "noise_seed": seed,
                "steps": steps,
                "cfg": cfg,
                "sampler_name": "euler",
                "scheduler": "simple",
                "positive": ["11", 0],
                "negative": ["12", 0],
                "latent_image": ["20", 0],
                "start_at_step": 0,
                "end_at_step": split_step,
                "return_with_leftover_noise": "enable",
            },
        ),
        "51": node(
            "KSamplerAdvanced",
            {
                "model": ["42", 0],
                "add_noise": "disable",
                "noise_seed": seed,
                "steps": steps,
                "cfg": cfg,
                "sampler_name": "euler",
                "scheduler": "simple",
                "positive": ["11", 0],
                "negative": ["12", 0],
                "latent_image": ["50", 0],
                "start_at_step": split_step,
                "end_at_step": steps,
                "return_with_leftover_noise": "disable",
            },
        ),
        "60": node(
            "VAEDecodeTiled",
            {
                "samples": ["51", 0],
                "vae": ["13", 0],
                "tile_size": 512,
                "overlap": 64,
                "temporal_size": 16,
                "temporal_overlap": 4,
            },
        ),
        "70": node("CreateVideo", {"images": ["60", 0], "fps": fps}),
        "80": node(
            "SaveVideo",
            {
                "video": ["70", 0],
                "filename_prefix": "wan22_frontend/T2V_A14B_%date:yyyy-MM-dd%",
                "format": "mp4",
                "codec": "h264",
            },
        ),
    }


def build_rife_prompt(*, video_name: str, fps: int, multiplier: int) -> dict[str, Any]:
    multiplier = clamp_int(multiplier, 2, 4)
    output_fps = clamp_int(fps * multiplier, 1, 120)
    return {
        "1": node(
            "VHS_LoadVideo",
            {
                "video": video_name,
                "force_rate": fps,
                "force_size": "Disabled",
                "custom_width": 0,
                "custom_height": 0,
                "frame_load_cap": 0,
                "skip_first_frames": 0,
                "select_every_nth": 1,
            },
        ),
        "2": node(
            "RIFE VFI",
            {
                "ckpt_name": "rife49.pth",
                "frames": ["1", 0],
                "clear_cache_after_n_frames": 10,
                "multiplier": multiplier,
                "fast_mode": True,
                "ensemble": True,
                "scale_factor": 1.0,
                "dtype": "float16",
                "torch_compile": False,
                "batch_size": 4,
            },
        ),
        "3": node(
            "VHS_VideoCombine",
            {
                "images": ["2", 0],
                "frame_rate": output_fps,
                "loop_count": 0,
                "filename_prefix": "wan22_frontend/RIFE_2x_%date:yyyy-MM-dd%",
                "format": "video/h264-mp4",
                "pix_fmt": "yuv420p",
                "crf": 19,
                "save_metadata": True,
                "trim_to_audio": False,
                "pingpong": False,
                "save_output": True,
            },
        ),
    }


def build_video_upscale_prompt(*, video_name: str, fps: int, model_name: str = "RealESRGAN_x2plus.pth") -> dict[str, Any]:
    fps = clamp_int(fps, 1, 120)
    allowed_models = {"RealESRGAN_x2plus.pth", "4x-UltraSharp.pth"}
    if model_name not in allowed_models:
        model_name = "RealESRGAN_x2plus.pth"
    return {
        "1": node(
            "VHS_LoadVideo",
            {
                "video": video_name,
                "force_rate": fps,
                "force_size": "Disabled",
                "custom_width": 0,
                "custom_height": 0,
                "frame_load_cap": 0,
                "skip_first_frames": 0,
                "select_every_nth": 1,
            },
        ),
        "2": node("UpscaleModelLoader", {"model_name": model_name}),
        "3": node(
            "ImageUpscaleWithModel",
            {
                "upscale_model": ["2", 0],
                "image": ["1", 0],
            },
        ),
        "4": node(
            "VHS_VideoCombine",
            {
                "images": ["3", 0],
                "frame_rate": fps,
                "loop_count": 0,
                "filename_prefix": "wan22_frontend/Upscale_2x_%date:yyyy-MM-dd%",
                "format": "video/h264-mp4",
                "pix_fmt": "yuv420p",
                "crf": 18,
                "save_metadata": True,
                "trim_to_audio": False,
                "pingpong": False,
                "save_output": True,
            },
        ),
    }


def build_preview_workflow_graph(
    *,
    mode: str,
    prompt: str,
    negative: str,
    width: int,
    height: int,
    length: int,
    fps: int,
    seed: int,
    steps: int,
    cfg: float,
    upscale_model: str,
    rife_multiplier: int,
) -> dict[str, Any] | None:
    if mode == "i2v_a14b":
        return build_a14b_i2v_prompt(
            prompt=prompt,
            negative=negative,
            image_name=DEFAULT_IMAGE,
            width=width,
            height=height,
            length=length,
            fps=fps,
            seed=seed,
            steps=steps,
            cfg=cfg,
        )
    if mode == "t2v_a14b":
        return build_a14b_t2v_prompt(
            prompt=prompt,
            negative=negative,
            width=width,
            height=height,
            length=length,
            fps=fps,
            seed=seed,
            steps=steps,
            cfg=cfg,
        )
    if mode == "ti2v_5b":
        return build_ti2v_prompt(
            prompt=prompt,
            negative=negative,
            image_name=DEFAULT_IMAGE,
            width=width,
            height=height,
            length=length,
            fps=fps,
            seed=seed,
            steps=steps,
            cfg=cfg,
        )
    if mode == "rife_2x":
        return build_rife_prompt(video_name="sample.mp4", fps=fps, multiplier=rife_multiplier)
    if mode == "upscale_2x":
        return build_video_upscale_prompt(video_name="sample.mp4", fps=fps, model_name=upscale_model)
    return None


def validate_graph(prompt: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if not any(item.get("class_type") in {"SaveVideo", "VHS_VideoCombine", "SaveImage"} for item in prompt.values()):
        errors.append("缺少输出节点")
    for node_id, item in prompt.items():
        inputs = item.get("inputs", {})
        for key, value in inputs.items():
            if (
                isinstance(value, list)
                and len(value) == 2
                and isinstance(value[0], str)
                and isinstance(value[1], int)
                and value[0] not in prompt
            ):
                errors.append(f"{node_id}.{key} 连接到不存在的节点 {value[0]}")
    return errors


def graph_class_types(prompt: dict[str, Any]) -> list[str]:
    return sorted({str(item.get("class_type", "")) for item in prompt.values() if item.get("class_type")})


async def comfy_node_availability(prompt: dict[str, Any]) -> dict[str, Any]:
    class_types = graph_class_types(prompt)
    try:
        object_info = await comfy_get("/object_info")
    except HTTPException as exc:
        return {
            "connected": False,
            "missing": class_types,
            "error": str(exc.detail),
        }
    missing = [name for name in class_types if name not in object_info]
    return {
        "connected": True,
        "missing": missing,
        "error": "",
    }


def media_url(item: dict[str, Any]) -> str:
    filename = item.get("filename", "")
    subfolder = item.get("subfolder", "")
    file_type = item.get("type", "output")
    return (
        f"/api/view?filename={quote(filename)}"
        f"&subfolder={quote(subfolder)}"
        f"&type={quote(file_type)}"
    )


def extract_media(history_item: dict[str, Any]) -> list[dict[str, Any]]:
    media: list[dict[str, Any]] = []
    outputs = history_item.get("outputs", {})
    for node_output in outputs.values():
        for key in ("videos", "gifs", "images"):
            for item in node_output.get(key, []) or []:
                filename = item.get("filename")
                if not filename:
                    continue
                suffix = Path(filename).suffix.lower()
                kind = "video" if suffix in VIDEO_EXTENSIONS else "image"
                media.append(
                    {
                        "kind": kind,
                        "filename": filename,
                        "subfolder": item.get("subfolder", ""),
                        "type": item.get("type", "output"),
                        "url": media_url(item),
                    }
                )
    return media


def safe_media_path(filename: str, subfolder: str, file_type: str) -> Path:
    roots = {
        "input": INPUT_DIR,
        "output": OUTPUT_DIR,
        "temp": TEMP_DIR,
    }
    root = roots.get(file_type)
    if root is None:
        raise HTTPException(status_code=400, detail="未知文件类型")

    base = root.resolve()
    candidate = (root / subfolder / filename).resolve()
    if not str(candidate).lower().startswith(str(base).lower()):
        raise HTTPException(status_code=400, detail="文件路径不安全")
    if not candidate.exists():
        raise HTTPException(status_code=404, detail="文件不存在")
    return candidate


def input_media_path(relative_name: str) -> Path:
    normalized = relative_name.replace("\\", "/")
    relative = Path(normalized)
    if relative.is_absolute() or ".." in relative.parts:
        raise HTTPException(status_code=400, detail="输入文件路径不安全")

    base = INPUT_DIR.resolve()
    candidate = (INPUT_DIR / relative).resolve()
    if not str(candidate).lower().startswith(str(base).lower()):
        raise HTTPException(status_code=400, detail="输入文件路径不安全")
    if not candidate.exists():
        raise HTTPException(status_code=404, detail="输入文件不存在")
    return candidate


def output_media_item(path: Path) -> dict[str, Any]:
    output_base = OUTPUT_DIR.resolve()
    resolved = path.resolve()
    try:
        relative = resolved.relative_to(output_base)
    except ValueError as exc:
        raise HTTPException(status_code=500, detail="输出文件不在 ComfyUI output 目录") from exc

    subfolder = "" if relative.parent == Path(".") else str(relative.parent).replace("\\", "/")
    item = {
        "filename": relative.name,
        "subfolder": subfolder,
        "type": "output",
    }
    return {
        "kind": "video" if resolved.suffix.lower() in VIDEO_EXTENSIONS else "image",
        **item,
        "url": media_url(item),
    }


def find_ffmpeg() -> str:
    ffmpeg = os.environ.get("FFMPEG_PATH") or shutil.which("ffmpeg")
    if ffmpeg:
        return ffmpeg

    try:
        from imageio_ffmpeg import get_ffmpeg_exe

        return get_ffmpeg_exe()
    except Exception as exc:
        raise HTTPException(status_code=503, detail="未找到 ffmpeg，无法执行闪烁修复") from exc


def ffmpeg_has_filter(filter_name: str) -> bool:
    try:
        result = subprocess.run(
            [find_ffmpeg(), "-hide_banner", "-filters"],
            capture_output=True,
            check=True,
            text=True,
            timeout=20,
        )
    except Exception:
        return False
    return filter_name in result.stdout


def workflow_asset_manifest() -> list[dict[str, Any]]:
    return [
        {
            "id": "ti2v_5b",
            "label": "Wan2.2 TI2V-5B fp16",
            "step": "试镜头",
            "path": BASE_DIR / "models" / "diffusion_models" / "Wan2.2" / "wan2.2_ti2v_5B_fp16.safetensors",
            "bytes": 9999658848,
            "installable": True,
        },
        {
            "id": "i2v_high",
            "label": "Wan2.2 I2V-A14B high noise fp8",
            "step": "正式片段",
            "path": BASE_DIR / "models" / "diffusion_models" / "Wan2.2" / "wan2.2_i2v_high_noise_14B_fp8_scaled.safetensors",
            "bytes": 14294742832,
            "installable": True,
        },
        {
            "id": "i2v_low",
            "label": "Wan2.2 I2V-A14B low noise fp8",
            "step": "正式片段",
            "path": BASE_DIR / "models" / "diffusion_models" / "Wan2.2" / "wan2.2_i2v_low_noise_14B_fp8_scaled.safetensors",
            "bytes": 14294742832,
            "installable": True,
        },
        {
            "id": "t2v_high",
            "label": "Wan2.2 T2V-A14B high noise fp8",
            "step": "文字生视频",
            "path": BASE_DIR / "models" / "diffusion_models" / "Wan2.2" / "wan2.2_t2v_high_noise_14B_fp8_scaled.safetensors",
            "bytes": 14293923632,
            "installable": True,
        },
        {
            "id": "t2v_low",
            "label": "Wan2.2 T2V-A14B low noise fp8",
            "step": "文字生视频",
            "path": BASE_DIR / "models" / "diffusion_models" / "Wan2.2" / "wan2.2_t2v_low_noise_14B_fp8_scaled.safetensors",
            "bytes": 14293923632,
            "installable": True,
        },
        {
            "id": "umt5",
            "label": "UMT5 XXL fp8 文本编码器",
            "step": "视频生成",
            "path": BASE_DIR / "models" / "text_encoders" / "umt5_xxl_fp8_e4m3fn_scaled.safetensors",
            "bytes": 6735906897,
            "installable": True,
        },
        {
            "id": "wan21_vae",
            "label": "Wan 2.1 VAE",
            "step": "A14B 正片",
            "path": BASE_DIR / "models" / "vae" / "wan_2.1_vae.safetensors",
            "bytes": 253815318,
            "installable": True,
        },
        {
            "id": "wan22_vae",
            "label": "Wan 2.2 VAE",
            "step": "TI2V 草稿",
            "path": BASE_DIR / "models" / "vae" / "wan2.2_vae.safetensors",
            "bytes": 1409400960,
            "installable": True,
        },
        {
            "id": "i2v_lora_high",
            "label": "I2V 4步 LoRA high noise",
            "step": "正式片段",
            "path": BASE_DIR / "models" / "loras" / "Wan2.2" / "wan2.2_i2v_lightx2v_4steps_lora_v1_high_noise.safetensors",
            "bytes": 1226977424,
            "installable": True,
        },
        {
            "id": "i2v_lora_low",
            "label": "I2V 4步 LoRA low noise",
            "step": "正式片段",
            "path": BASE_DIR / "models" / "loras" / "Wan2.2" / "wan2.2_i2v_lightx2v_4steps_lora_v1_low_noise.safetensors",
            "bytes": 1226977424,
            "installable": True,
        },
        {
            "id": "t2v_lora_high",
            "label": "T2V 4步 LoRA high noise",
            "step": "文字生视频",
            "path": BASE_DIR / "models" / "loras" / "Wan2.2" / "wan2.2_t2v_lightx2v_4steps_lora_v1.1_high_noise.safetensors",
            "bytes": 1226977424,
            "installable": True,
        },
        {
            "id": "t2v_lora_low",
            "label": "T2V 4步 LoRA low noise",
            "step": "文字生视频",
            "path": BASE_DIR / "models" / "loras" / "Wan2.2" / "wan2.2_t2v_lightx2v_4steps_lora_v1.1_low_noise.safetensors",
            "bytes": 1226977424,
            "installable": True,
        },
        {
            "id": "rife49",
            "label": "RIFE 4.9 插帧权重",
            "step": "RIFE 插帧",
            "path": BASE_DIR / "custom_nodes" / "ComfyUI-Frame-Interpolation" / "ckpts" / "rife" / "rife49.pth",
            "bytes": 21345274,
            "installable": True,
        },
        {
            "id": "realesrgan_x2",
            "label": "RealESRGAN x2 视频超分权重",
            "step": "清晰度增强",
            "path": BASE_DIR / "models" / "upscale_models" / "RealESRGAN_x2plus.pth",
            "bytes": 67061725,
            "installable": True,
        },
        {
            "id": "ultrasharp_x4",
            "label": "4x-UltraSharp 备用超分权重",
            "step": "清晰度增强",
            "path": BASE_DIR / "models" / "upscale_models" / "4x-UltraSharp.pth",
            "bytes": 66961958,
            "installable": True,
        },
        {
            "id": "sample_keyframe",
            "label": "内置示例关键帧",
            "step": "关键帧",
            "path": INPUT_DIR / DEFAULT_IMAGE,
            "bytes": None,
            "installable": True,
        },
    ]


def custom_node_manifest() -> list[dict[str, Any]]:
    return [
        {
            "id": "video_helper_suite",
            "label": "ComfyUI-VideoHelperSuite",
            "path": BASE_DIR / "custom_nodes" / "ComfyUI-VideoHelperSuite",
            "installable": True,
        },
        {
            "id": "frame_interpolation",
            "label": "ComfyUI-Frame-Interpolation",
            "path": BASE_DIR / "custom_nodes" / "ComfyUI-Frame-Interpolation",
            "installable": True,
        },
    ]


def path_label(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(BASE_DIR.resolve())).replace("\\", "/")
    except ValueError:
        return str(path)


def collect_asset_checks() -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    for item in workflow_asset_manifest():
        path = item["path"]
        exists = path.exists()
        size = path.stat().st_size if exists else 0
        expected = item.get("bytes")
        size_ok = exists and (not expected or size == expected)
        if not exists:
            reason = "缺失"
        elif expected and size != expected:
            reason = f"大小不一致：{size} / {expected} bytes"
        else:
            reason = "已就绪"
        checks.append(
            {
                "id": item["id"],
                "label": item["label"],
                "step": item["step"],
                "path": str(path),
                "relative_path": path_label(path),
                "ok": bool(size_ok),
                "exists": exists,
                "size_gb": round(size / 1024**3, 2) if exists else 0,
                "expected_gb": round(expected / 1024**3, 2) if expected else None,
                "installable": bool(item.get("installable")),
                "reason": reason,
            }
        )
    return checks


def collect_custom_node_checks() -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    for item in custom_node_manifest():
        path = item["path"]
        ok = path.exists() and path.is_dir()
        checks.append(
            {
                "id": item["id"],
                "label": item["label"],
                "path": str(path),
                "relative_path": path_label(path),
                "ok": ok,
                "installable": bool(item.get("installable")),
                "reason": "已就绪" if ok else "缺失",
            }
        )
    return checks


def get_system_memory_gb() -> float | None:
    system_name = platform.system()
    try:
        if system_name == "Windows":
            import ctypes

            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("sullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            memory = MEMORYSTATUSEX()
            memory.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(memory))
            return round(memory.ullTotalPhys / 1024**3, 1)
        if system_name == "Darwin":
            result = subprocess.run(
                ["sysctl", "-n", "hw.memsize"],
                capture_output=True,
                check=True,
                text=True,
                timeout=5,
            )
            return round(int(result.stdout.strip()) / 1024**3, 1)
        meminfo = Path("/proc/meminfo")
        if meminfo.exists():
            for line in meminfo.read_text(encoding="utf-8", errors="ignore").splitlines():
                if line.startswith("MemTotal:"):
                    kb = int(line.split()[1])
                    return round(kb / 1024**2, 1)
    except Exception:
        return None
    return None


def collect_torch_hardware() -> dict[str, Any]:
    info: dict[str, Any] = {
        "available": False,
        "version": None,
        "cuda_available": False,
        "mps_available": False,
        "devices": [],
    }
    try:
        import torch

        info["available"] = True
        info["version"] = getattr(torch, "__version__", None)
        info["cuda_available"] = bool(torch.cuda.is_available())
        if info["cuda_available"]:
            for index in range(torch.cuda.device_count()):
                props = torch.cuda.get_device_properties(index)
                info["devices"].append(
                    {
                        "index": index,
                        "name": torch.cuda.get_device_name(index),
                        "type": "cuda",
                        "vram_total_gb": round(props.total_memory / 1024**3, 1),
                    }
                )
        mps_backend = getattr(getattr(torch, "backends", None), "mps", None)
        if mps_backend is not None:
            info["mps_available"] = bool(mps_backend.is_available())
    except Exception as exc:
        info["error"] = str(exc)
    return info


def summarize_comfy_devices(system_stats: dict[str, Any] | None) -> list[dict[str, Any]]:
    devices: list[dict[str, Any]] = []
    for index, device in enumerate((system_stats or {}).get("devices") or []):
        vram_total = device.get("vram_total") or device.get("total_memory") or 0
        vram_free = device.get("vram_free") or device.get("free_memory") or 0
        devices.append(
            {
                "index": index,
                "name": device.get("name") or f"Device {index}",
                "type": device.get("type") or device.get("device_type") or "",
                "vram_total_gb": round(vram_total / 1024**3, 1) if vram_total else None,
                "vram_free_gb": round(vram_free / 1024**3, 1) if vram_free else None,
            }
        )
    return devices


def build_hardware_summary(system_stats: dict[str, Any] | None) -> dict[str, Any]:
    torch_info = collect_torch_hardware()
    comfy_devices = summarize_comfy_devices(system_stats)
    devices = comfy_devices or torch_info.get("devices") or []
    max_vram = max((device.get("vram_total_gb") or 0 for device in devices), default=0)
    sum_vram = round(sum((device.get("vram_total_gb") or 0 for device in devices)), 1)
    has_cuda = bool(torch_info.get("cuda_available")) or any(
        "cuda" in str(device.get("type", "")).lower() or "nvidia" in str(device.get("name", "")).lower()
        for device in devices
    )
    has_mps = bool(torch_info.get("mps_available"))
    accelerator = "cuda" if has_cuda else "mps" if has_mps else "cpu"
    return {
        "accelerator": accelerator,
        "devices": devices,
        "gpu_count": len(devices),
        "max_vram_gb": round(max_vram, 1),
        "sum_vram_gb": sum_vram,
        "system_memory_gb": get_system_memory_gb(),
        "torch": torch_info,
    }


def collect_node_checks(object_info: dict[str, Any] | None) -> list[dict[str, Any]]:
    required_nodes = [
        "SaveVideo",
        "CreateVideo",
        "LoadImage",
        "UNETLoader",
        "CLIPLoader",
        "VAELoader",
        "CLIPTextEncode",
        "WanImageToVideo",
        "Wan22ImageToVideoLatent",
        "EmptyHunyuanLatentVideo",
        "KSampler",
        "KSamplerAdvanced",
        "VAEDecodeTiled",
        "LoraLoaderModelOnly",
        "ModelSamplingSD3",
        "VHS_LoadVideo",
        "VHS_VideoCombine",
        "RIFE VFI",
        "UpscaleModelLoader",
        "ImageUpscaleWithModel",
    ]
    if object_info is None:
        return [{"name": name, "ok": False, "reason": "ComfyUI 未连接"} for name in required_nodes]
    return [{"name": name, "ok": name in object_info, "reason": "已加载" if name in object_info else "缺少"} for name in required_nodes]


def model_recommendations(
    *,
    os_info: dict[str, Any],
    hardware: dict[str, Any],
    assets: list[dict[str, Any]],
    custom_nodes: list[dict[str, Any]],
    tool_checks: list[dict[str, Any]],
    node_checks: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    asset_ok = {item["id"]: bool(item["ok"]) for item in assets}
    node_ok = {item["name"]: bool(item["ok"]) for item in node_checks}
    custom_ok = {item["id"]: bool(item["ok"]) for item in custom_nodes}
    tool_ok = {item["name"]: bool(item["ok"]) for item in tool_checks}
    accelerator = hardware.get("accelerator")
    max_vram = float(hardware.get("max_vram_gb") or 0)
    sum_vram = float(hardware.get("sum_vram_gb") or 0)
    gpu_count = int(hardware.get("gpu_count") or 0)
    is_macos = os_info["name"] == "Darwin"

    def all_assets(*ids: str) -> bool:
        return all(asset_ok.get(item) for item in ids)

    def all_nodes(*names: str) -> bool:
        return all(node_ok.get(item) for item in names)

    recommendations: list[dict[str, Any]] = []

    has_flux = (BASE_DIR / "models" / "diffusion_models" / "flux2_dev_fp8mixed.safetensors").exists()
    has_sdxl = any((BASE_DIR / "models" / "checkpoints").glob("*.safetensors"))
    keyframe_models = ["上传 PNG/JPG 关键帧"]
    if has_flux:
        keyframe_models.insert(0, "Flux/Flux2 本地生图")
    if has_sdxl:
        keyframe_models.insert(0, "SDXL 本地生图")
    recommendations.append(
        {
            "step": "关键帧",
            "status": "ok" if has_flux or has_sdxl else "warn",
            "recommended": keyframe_models,
            "reason": "检测到本地生图模型，可先生成首帧。" if has_flux or has_sdxl else "未检测到本地生图模型，也可以直接上传外部生成的关键帧。",
        }
    )

    ti2v_ready = all_assets("ti2v_5b", "umt5", "wan22_vae") and all_nodes(
        "Wan22ImageToVideoLatent", "UNETLoader", "CLIPLoader", "VAELoader"
    )
    if not ti2v_ready:
        status_text = "blocked"
        reason = "TI2V-5B 所需模型或 ComfyUI 节点不完整。"
    elif accelerator == "cuda" and max_vram >= 24:
        status_text = "ok"
        reason = f"单卡可用显存档位足够跑 720P 草稿，检测到最高单卡约 {max_vram:g}GB。"
    elif accelerator == "cuda" and max_vram >= 16:
        status_text = "warn"
        reason = "可尝试较短镜头或降低分辨率；建议开启 offload。"
    elif is_macos and accelerator == "mps":
        status_text = "warn"
        reason = "macOS 可作为兼容目标，但 Wan 视频节点在 MPS 上通常更慢且更挑版本。"
    else:
        status_text = "blocked"
        reason = "未检测到适合本地视频生成的 GPU 后端。"
    recommendations.append(
        {
            "step": "试镜头",
            "status": status_text,
            "recommended": ["Wan2.2 TI2V-5B 720P", "横屏 1280x704 / 竖屏 704x1280", "3 到 5 秒短镜头"],
            "reason": reason,
        }
    )

    i2v_ready = all_assets(
        "i2v_high",
        "i2v_low",
        "umt5",
        "wan21_vae",
        "i2v_lora_high",
        "i2v_lora_low",
    ) and all_nodes("WanImageToVideo", "KSamplerAdvanced", "LoraLoaderModelOnly")
    if not i2v_ready:
        status_text = "blocked"
        reason = "A14B I2V 所需模型、LoRA、VAE 或节点不完整。"
    elif accelerator == "cuda" and max_vram >= 80:
        status_text = "ok"
        reason = f"适合跑 A14B 720P 正片，最高单卡约 {max_vram:g}GB。"
    elif accelerator == "cuda" and max_vram >= 48:
        status_text = "warn"
        reason = "48GB 更适合 480P、短镜头或 offload；A14B 720P 默认仍可能 OOM。"
    elif gpu_count >= 2 and sum_vram >= 80:
        status_text = "warn"
        reason = "多卡总显存足够，但单个 ComfyUI 视频任务通常更看单卡显存；可并行跑镜头，不等价于单卡 96GB。"
    else:
        status_text = "blocked"
        reason = "A14B 正片建议至少 80GB 单卡显存；当前环境不建议直接跑 720P。"
    recommendations.append(
        {
            "step": "正式片段",
            "status": status_text,
            "recommended": ["Wan2.2 I2V-A14B fp8 + 4步 LoRA", "优先图生视频", "720P 3 到 5 秒"],
            "reason": reason,
        }
    )

    t2v_ready = all_assets(
        "t2v_high",
        "t2v_low",
        "umt5",
        "wan21_vae",
        "t2v_lora_high",
        "t2v_lora_low",
    ) and all_nodes("EmptyHunyuanLatentVideo", "KSamplerAdvanced", "LoraLoaderModelOnly")
    recommendations.append(
        {
            "step": "无图生视频",
            "status": "ok" if t2v_ready and accelerator == "cuda" and max_vram >= 80 else "warn" if t2v_ready else "blocked",
            "recommended": ["Wan2.2 T2V-A14B fp8 + 4步 LoRA"],
            "reason": "可用，但品牌、人物和空间一致性不如 I2V；建议只在没有关键帧时使用。"
            if t2v_ready
            else "T2V-A14B 所需模型或节点不完整。",
        }
    )

    deflicker_ok = tool_ok.get("ffmpeg") and tool_ok.get("ffmpeg:deflicker") and tool_ok.get("ffmpeg:hqdn3d")
    recommendations.append(
        {
            "step": "闪烁修复",
            "status": "ok" if deflicker_ok else "blocked",
            "recommended": ["ffmpeg deflicker + hqdn3d"],
            "reason": "可修正曝光闪烁和轻微纹理跳动；人物结构变形仍要回到生成步骤重跑。"
            if deflicker_ok
            else "未检测到 ffmpeg 或所需滤镜。",
        }
    )

    rife_ready = asset_ok.get("rife49") and custom_ok.get("frame_interpolation") and all_nodes("RIFE VFI")
    recommendations.append(
        {
            "step": "插帧",
            "status": "ok" if rife_ready and accelerator == "cuda" else "warn" if rife_ready else "blocked",
            "recommended": ["RIFE 4.9 2x 插帧"],
            "reason": "CUDA 下适合把 24fps 提到 48fps。"
            if rife_ready and accelerator == "cuda"
            else "权重和节点已在，但非 CUDA 后端可能很慢或需要额外兼容配置。"
            if rife_ready
            else "RIFE 权重或自定义节点未就绪。",
        }
    )

    upscale_ready = asset_ok.get("realesrgan_x2") and all_nodes("UpscaleModelLoader", "ImageUpscaleWithModel")
    recommendations.append(
        {
            "step": "清晰度增强",
            "status": "ok" if upscale_ready and accelerator == "cuda" else "warn" if upscale_ready else "blocked",
            "recommended": ["RealESRGAN_x2plus 2x", "4x-UltraSharp 备用"],
            "reason": "默认把 1280x704 提到 2560x1408，再交给剪辑软件导出 1080P/4K。"
            if upscale_ready
            else "超分权重或 ComfyUI 超分节点未就绪。",
        }
    )

    recommendations.append(
        {
            "step": "拼接剪辑",
            "status": "ok",
            "recommended": ["剪映", "DaVinci Resolve", "Premiere Pro"],
            "reason": "模型负责短镜头，剪辑软件负责节奏、调色、字幕和声音。",
        }
    )
    return recommendations


def workflow_model_options(
    *,
    os_info: dict[str, Any],
    hardware: dict[str, Any],
    assets: list[dict[str, Any]],
    custom_nodes: list[dict[str, Any]],
    tool_checks: list[dict[str, Any]],
    node_checks: list[dict[str, Any]],
) -> dict[str, Any]:
    asset_ok = {item["id"]: bool(item["ok"]) for item in assets}
    node_ok = {item["name"]: bool(item["ok"]) for item in node_checks}
    custom_ok = {item["id"]: bool(item["ok"]) for item in custom_nodes}
    tool_ok = {item["name"]: bool(item["ok"]) for item in tool_checks}
    accelerator = hardware.get("accelerator")
    max_vram = float(hardware.get("max_vram_gb") or 0)
    is_macos = os_info["name"] == "Darwin"

    def all_assets(*ids: str) -> bool:
        return all(asset_ok.get(item) for item in ids)

    def all_nodes(*names: str) -> bool:
        return all(node_ok.get(item) for item in names)

    def cuda_status(*, ready: bool, ok_gb: float, warn_gb: float, mac_warn: bool = False) -> str:
        if not ready:
            return "blocked"
        if accelerator == "cuda" and max_vram >= ok_gb:
            return "ok"
        if accelerator == "cuda" and max_vram >= warn_gb:
            return "warn"
        if is_macos and accelerator == "mps" and mac_warn:
            return "warn"
        return "blocked"

    def option(
        *,
        id: str,
        label: str,
        step: str,
        mode: str,
        status: str,
        reason: str,
        defaults: dict[str, Any] | None = None,
        uses_image: bool = True,
        model_label: str | None = None,
        supported: bool = True,
        scale: int | None = None,
        upscale_model: str | None = None,
        rife_multiplier: int | None = None,
    ) -> dict[str, Any]:
        return {
            "id": id,
            "label": label,
            "step": step,
            "mode": mode,
            "status": status,
            "reason": reason,
            "defaults": defaults or {},
            "uses_image": uses_image,
            "model_label": model_label or label,
            "supported": supported,
            "scale": scale,
            "upscale_model": upscale_model,
            "rife_multiplier": rife_multiplier,
        }

    ti2v_ready = all_assets("ti2v_5b", "umt5", "wan22_vae") and all_nodes(
        "Wan22ImageToVideoLatent", "UNETLoader", "CLIPLoader", "VAELoader", "KSampler"
    )
    i2v_ready = all_assets(
        "i2v_high",
        "i2v_low",
        "umt5",
        "wan21_vae",
        "i2v_lora_high",
        "i2v_lora_low",
    ) and all_nodes("WanImageToVideo", "KSamplerAdvanced", "LoraLoaderModelOnly")
    t2v_ready = all_assets(
        "t2v_high",
        "t2v_low",
        "umt5",
        "wan21_vae",
        "t2v_lora_high",
        "t2v_lora_low",
    ) and all_nodes("EmptyHunyuanLatentVideo", "KSamplerAdvanced", "LoraLoaderModelOnly")
    deflicker_ready = bool(tool_ok.get("ffmpeg") and tool_ok.get("ffmpeg:deflicker") and tool_ok.get("ffmpeg:hqdn3d"))
    rife_ready = bool(asset_ok.get("rife49") and custom_ok.get("frame_interpolation") and all_nodes("RIFE VFI"))
    upscale_ready = all_nodes("UpscaleModelLoader", "ImageUpscaleWithModel")
    has_flux = (BASE_DIR / "models" / "diffusion_models" / "flux2_dev_fp8mixed.safetensors").exists()
    has_sdxl = any((BASE_DIR / "models" / "checkpoints").glob("*.safetensors"))

    options = {
        "keyframe": [
            option(
                id="upload_keyframe",
                label="上传/外部关键帧",
                step="keyframe",
                mode="",
                status="ok",
                reason="最稳妥，任何硬件都可用；先把构图定死再做视频。",
                uses_image=True,
            ),
            option(
                id="sdxl_keyframe",
                label="SDXL 本地生图",
                step="keyframe",
                mode="",
                status="ok" if has_sdxl else "warn",
                reason="适合 8GB 以上 CUDA 或 Apple Silicon 生成关键帧；未接入自动生图按钮时可在 ComfyUI 里生成后上传。",
                uses_image=True,
                supported=False,
            ),
            option(
                id="flux_keyframe",
                label="Flux/Flux2 本地生图",
                step="keyframe",
                mode="",
                status="ok" if has_flux and (max_vram >= 24 or is_macos) else "warn",
                reason="画质和商业摄影感更强；低显存或 macOS 需要更谨慎的量化/低显存工作流。",
                uses_image=True,
                supported=False,
            ),
        ],
        "draft": [
            option(
                id="wan22_ti2v_5b_720p",
                label="Wan2.2 TI2V-5B 720P",
                step="draft",
                mode="ti2v_5b",
                status=cuda_status(ready=ti2v_ready, ok_gb=24, warn_gb=16, mac_warn=True),
                reason="推荐草稿模型。24GB 以上 CUDA 更舒服，macOS 可实验但速度和节点兼容性要看环境。",
                defaults={"width": 1280, "height": 704, "length": 81, "fps": 24, "steps": 20, "cfg": 5.0},
            ),
            option(
                id="wan22_ti2v_5b_480p",
                label="Wan2.2 TI2V-5B 480P 小显存",
                step="draft",
                mode="ti2v_5b",
                status=cuda_status(ready=ti2v_ready, ok_gb=16, warn_gb=8, mac_warn=True),
                reason="给 8GB 到 16GB、Apple Silicon 或不稳定机器的保守档；更慢但更容易跑通。",
                defaults={"width": 832, "height": 480, "length": 49, "fps": 24, "steps": 18, "cfg": 5.0},
            ),
        ],
        "final": [
            option(
                id="wan22_i2v_a14b_720p",
                label="Wan2.2 I2V-A14B 720P",
                step="final",
                mode="i2v_a14b",
                status=cuda_status(ready=i2v_ready, ok_gb=80, warn_gb=64),
                reason="96GB/80GB 单卡首选，用关键帧控制画面，适合正式出片。",
                defaults={"width": 1280, "height": 704, "length": 81, "fps": 24, "steps": 4, "cfg": 1.0},
            ),
            option(
                id="wan22_i2v_a14b_480p",
                label="Wan2.2 I2V-A14B 480P",
                step="final",
                mode="i2v_a14b",
                status=cuda_status(ready=i2v_ready, ok_gb=48, warn_gb=32),
                reason="48GB 单卡更现实的 A14B 档位；可先 480P 出片，再做超分。",
                defaults={"width": 832, "height": 480, "length": 49, "fps": 24, "steps": 4, "cfg": 1.0},
            ),
            option(
                id="wan22_t2v_a14b_720p",
                label="Wan2.2 T2V-A14B 720P",
                step="final",
                mode="t2v_a14b",
                status=cuda_status(ready=t2v_ready, ok_gb=80, warn_gb=64),
                reason="没有关键帧时使用；随机性更大，品牌和人物一致性不如 I2V。",
                defaults={"width": 1280, "height": 704, "length": 81, "fps": 24, "steps": 4, "cfg": 1.0},
                uses_image=False,
            ),
            option(
                id="wan22_t2v_a14b_480p",
                label="Wan2.2 T2V-A14B 480P",
                step="final",
                mode="t2v_a14b",
                status=cuda_status(ready=t2v_ready, ok_gb=48, warn_gb=32),
                reason="48GB 或更紧张显存的文字生视频尝试档；仍建议优先 I2V。",
                defaults={"width": 832, "height": 480, "length": 49, "fps": 24, "steps": 4, "cfg": 1.0},
                uses_image=False,
            ),
            option(
                id="wan22_ti2v_5b_final_720p",
                label="Wan2.2 TI2V-5B 720P 轻量正片",
                step="final",
                mode="ti2v_5b",
                status=cuda_status(ready=ti2v_ready, ok_gb=24, warn_gb=16, mac_warn=True),
                reason="24GB 到 48GB 可以把 5B 当最终模型用，画质不如 A14B，但更容易跑通。",
                defaults={"width": 1280, "height": 704, "length": 81, "fps": 24, "steps": 24, "cfg": 5.0},
            ),
            option(
                id="wan22_ti2v_5b_final_480p",
                label="Wan2.2 TI2V-5B 480P 小显存正片",
                step="final",
                mode="ti2v_5b",
                status=cuda_status(ready=ti2v_ready, ok_gb=16, warn_gb=8, mac_warn=True),
                reason="低配 CUDA、Apple Silicon 或测试机的保底视频生成档。",
                defaults={"width": 832, "height": 480, "length": 49, "fps": 24, "steps": 22, "cfg": 5.0},
            ),
        ],
        "deflicker": [
            option(
                id="ffmpeg_deflicker_balanced",
                label="ffmpeg deflicker + hqdn3d",
                step="deflicker",
                mode="deflicker",
                status="ok" if deflicker_ready else "blocked",
                reason="CPU 也能跑，适合修曝光闪烁和轻微纹理跳动。",
                uses_image=False,
            )
        ],
        "rife": [
            option(
                id="rife49_2x",
                label="RIFE 4.9 2x",
                step="rife",
                mode="rife_2x",
                status="ok" if rife_ready and accelerator == "cuda" else "warn" if rife_ready else "blocked",
                reason="把 24fps 插到 48fps；CUDA 最稳，macOS/CPU 可能很慢。",
                uses_image=False,
                rife_multiplier=2,
            ),
            option(
                id="rife49_4x",
                label="RIFE 4.9 4x",
                step="rife",
                mode="rife_2x",
                status="ok" if rife_ready and accelerator == "cuda" and max_vram >= 16 else "warn" if rife_ready else "blocked",
                reason="更高帧率，耗时和伪影风险更高；只建议短片段。",
                uses_image=False,
                rife_multiplier=4,
            ),
        ],
        "upscale": [
            option(
                id="realesrgan_x2plus",
                label="RealESRGAN x2plus 2x",
                step="upscale",
                mode="upscale_2x",
                status="ok" if upscale_ready and asset_ok.get("realesrgan_x2") else "blocked",
                reason="默认超分，稳妥地把 720P 提到约 2x。",
                uses_image=False,
                scale=2,
                upscale_model="RealESRGAN_x2plus.pth",
            ),
            option(
                id="ultrasharp_4x",
                label="4x-UltraSharp 4x",
                step="upscale",
                mode="upscale_2x",
                status="ok" if upscale_ready and asset_ok.get("ultrasharp_x4") and max_vram >= 16 else "warn" if asset_ok.get("ultrasharp_x4") else "blocked",
                reason="目标分辨率更高，文件和显存压力明显增加；适合最终短片段。",
                uses_image=False,
                scale=4,
                upscale_model="4x-UltraSharp.pth",
            ),
        ],
    }

    def first_ok(step: str, fallback: str) -> str:
        for item in options[step]:
            if item["status"] == "ok" and item["supported"]:
                return item["id"]
        for item in options[step]:
            if item["status"] == "warn" and item["supported"]:
                return item["id"]
        return fallback

    if accelerator == "cuda" and max_vram >= 80:
        final_default = "wan22_i2v_a14b_720p"
        draft_default = "wan22_ti2v_5b_720p"
    elif accelerator == "cuda" and max_vram >= 48:
        final_default = "wan22_i2v_a14b_480p"
        draft_default = "wan22_ti2v_5b_720p"
    elif accelerator == "cuda" and max_vram >= 16:
        final_default = "wan22_ti2v_5b_final_480p"
        draft_default = "wan22_ti2v_5b_480p"
    elif is_macos and accelerator == "mps":
        final_default = "wan22_ti2v_5b_final_480p"
        draft_default = "wan22_ti2v_5b_480p"
    else:
        final_default = first_ok("final", "wan22_ti2v_5b_final_480p")
        draft_default = first_ok("draft", "wan22_ti2v_5b_480p")

    recommended = {
        "keyframe": "flux_keyframe" if has_flux else "sdxl_keyframe" if has_sdxl else "upload_keyframe",
        "draft": draft_default,
        "final": final_default,
        "deflicker": first_ok("deflicker", "ffmpeg_deflicker_balanced"),
        "rife": first_ok("rife", "rife49_2x"),
        "upscale": "realesrgan_x2plus",
    }

    small_model_routes = [
        {
            "hardware": "8GB-12GB CUDA / 入门显卡",
            "route": "关键帧 + Wan2.2 TI2V-5B 480P 短镜头，或另接 LTX-Video / AnimateDiff 小模型工作流。",
        },
        {
            "hardware": "16GB-24GB CUDA",
            "route": "Wan2.2 TI2V-5B 480P/720P；A14B 不建议作为主力。",
        },
        {
            "hardware": "48GB CUDA",
            "route": "TI2V-5B 720P 稳定可用；A14B 建议 480P、短镜头、必要时 offload。",
        },
        {
            "hardware": "80GB-96GB CUDA",
            "route": "Wan2.2 I2V-A14B 720P 主线；TI2V-5B 做草稿。",
        },
        {
            "hardware": "Apple Silicon / macOS",
            "route": "优先关键帧、剪辑、ffmpeg 后期；可尝试 TI2V-5B 480P 实验档，或另接 LTX-Video / AnimateDiff / CoreML 轻量工作流。",
        },
        {
            "hardware": "CPU / 无独显",
            "route": "不建议本地生视频；适合关键帧管理、剪辑拼接、ffmpeg 闪烁修复。",
        },
    ]

    return {
        "options": options,
        "recommended": recommended,
        "small_model_routes": small_model_routes,
    }


def installable_missing_items(assets: list[dict[str, Any]], custom_nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    missing: list[dict[str, Any]] = []
    for item in assets + custom_nodes:
        if not item.get("ok") and item.get("installable"):
            missing.append(
                {
                    "id": item.get("id"),
                    "label": item.get("label"),
                    "path": item.get("relative_path") or item.get("path"),
                    "reason": item.get("reason"),
                }
            )
    return missing


def install_command_text(command: list[str]) -> str:
    if platform.system() == "Windows":
        return subprocess.list2cmdline(command)
    return " ".join(command)


def run_install_worker(job_id: str, command: list[str]) -> None:
    job = INSTALL_JOBS[job_id]
    job["status"] = "running"
    job["started_at"] = now_ms()
    job["log"].append(f"启动安装：{install_command_text(command)}")
    try:
        process = subprocess.Popen(
            command,
            cwd=str(WORKSPACE_DIR),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        assert process.stdout is not None
        for line in process.stdout:
            job["log"].append(line.rstrip())
            if len(job["log"]) > 600:
                job["log"] = job["log"][-600:]
        return_code = process.wait()
        job["return_code"] = return_code
        job["status"] = "success" if return_code == 0 else "failed"
        job["completed"] = True
        job["finished_at"] = now_ms()
        if return_code == 0:
            job["log"].append("安装/校验完成。请重启 ComfyUI，让新节点和模型列表重新加载。")
        else:
            job["log"].append(f"安装脚本退出码：{return_code}")
    except Exception as exc:
        job["status"] = "failed"
        job["completed"] = True
        job["finished_at"] = now_ms()
        job["error"] = str(exc)
        job["log"].append(f"安装失败：{exc}")


def run_deflicker(source: Path) -> dict[str, Any]:
    if source.suffix.lower() not in VIDEO_EXTENSIONS:
        raise HTTPException(status_code=400, detail="闪烁修复只支持视频文件")

    output_dir = OUTPUT_DIR / "wan22_frontend"
    output_dir.mkdir(parents=True, exist_ok=True)
    output = output_dir / f"Deflicker_{time.strftime('%Y-%m-%d_%H%M%S')}_{uuid.uuid4().hex[:6]}.mp4"

    filter_chain = "deflicker=s=8:m=pm,hqdn3d=1.2:1.2:4:4,format=yuv420p"
    command = [
        find_ffmpeg(),
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(source),
        "-map",
        "0:v:0",
        "-map",
        "0:a?",
        "-vf",
        filter_chain,
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-crf",
        "18",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-movflags",
        "+faststart",
        str(output),
    ]

    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        if output.exists():
            output.unlink()
        detail = result.stderr.strip()[-2000:] or "ffmpeg 闪烁修复失败"
        raise HTTPException(status_code=500, detail=detail)

    return output_media_item(output)


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/status")
async def status() -> dict[str, Any]:
    system_stats = await comfy_get("/system_stats")
    queue = await comfy_get("/queue")
    device = (system_stats.get("devices") or [{}])[0]
    return {
        "comfy_url": COMFY_URL,
        "comfyui_version": system_stats.get("system", {}).get("comfyui_version"),
        "pytorch_version": system_stats.get("system", {}).get("pytorch_version"),
        "device_name": device.get("name"),
        "vram_total_gb": round((device.get("vram_total") or 0) / 1024**3, 1),
        "vram_free_gb": round((device.get("vram_free") or 0) / 1024**3, 1),
        "queue_running": len(queue.get("queue_running") or []),
        "queue_pending": len(queue.get("queue_pending") or []),
    }


@app.get("/api/environment")
async def environment() -> dict[str, Any]:
    os_info = {
        "name": platform.system(),
        "release": platform.release(),
        "version": platform.version(),
        "machine": platform.machine(),
        "processor": platform.processor(),
        "python": sys.version.split()[0],
        "python_executable": sys.executable,
    }

    system_stats: dict[str, Any] | None = None
    object_info: dict[str, Any] | None = None
    comfy_error = ""
    queue_error = ""
    queue_info: dict[str, Any] = {"running": None, "pending": None}

    try:
        system_stats = await comfy_get("/system_stats")
    except HTTPException as exc:
        comfy_error = str(exc.detail)

    try:
        object_info = await comfy_get("/object_info")
    except HTTPException as exc:
        if not comfy_error:
            comfy_error = str(exc.detail)

    try:
        queue = await comfy_get("/queue")
        queue_info = {
            "running": len(queue.get("queue_running") or []),
            "pending": len(queue.get("queue_pending") or []),
        }
    except HTTPException as exc:
        queue_error = str(exc.detail)

    assets = collect_asset_checks()
    custom_nodes = collect_custom_node_checks()
    node_checks = collect_node_checks(object_info)

    try:
        ffmpeg_path = find_ffmpeg()
        ffmpeg_ok = True
    except HTTPException:
        ffmpeg_path = ""
        ffmpeg_ok = False

    tool_checks = [
        {"name": "ffmpeg", "ok": ffmpeg_ok, "path": ffmpeg_path},
        {"name": "ffmpeg:deflicker", "ok": ffmpeg_has_filter("deflicker"), "path": ffmpeg_path},
        {"name": "ffmpeg:hqdn3d", "ok": ffmpeg_has_filter("hqdn3d"), "path": ffmpeg_path},
    ]
    hardware = build_hardware_summary(system_stats)
    comfy_system = (system_stats or {}).get("system", {})
    comfy_info = {
        "url": COMFY_URL,
        "connected": system_stats is not None,
        "error": comfy_error,
        "queue_error": queue_error,
        "queue": queue_info,
        "comfyui_version": comfy_system.get("comfyui_version"),
        "pytorch_version": comfy_system.get("pytorch_version"),
        "python_version": comfy_system.get("python_version"),
    }
    recommendations = model_recommendations(
        os_info=os_info,
        hardware=hardware,
        assets=assets,
        custom_nodes=custom_nodes,
        tool_checks=tool_checks,
        node_checks=node_checks,
    )
    model_options = workflow_model_options(
        os_info=os_info,
        hardware=hardware,
        assets=assets,
        custom_nodes=custom_nodes,
        tool_checks=tool_checks,
        node_checks=node_checks,
    )
    missing_installable = installable_missing_items(assets, custom_nodes)
    core_steps = {"试镜头", "正式片段", "闪烁修复", "插帧", "清晰度增强"}
    blocked = [
        item
        for item in recommendations
        if item["step"] in core_steps and item["status"] == "blocked"
    ]

    return {
        "ok": bool(comfy_info["connected"] and not blocked),
        "base_dir": str(BASE_DIR),
        "workspace_dir": str(WORKSPACE_DIR),
        "os": os_info,
        "comfy": comfy_info,
        "hardware": hardware,
        "assets": assets,
        "custom_nodes": custom_nodes,
        "nodes": node_checks,
        "tools": tool_checks,
        "recommendations": recommendations,
        "model_options": model_options,
        "missing_installable": missing_installable,
        "needs_install": bool(missing_installable),
        "blocked": blocked,
        "install_note": "一键安装会下载缺失模型、RIFE/超分权重，并补齐两个 ComfyUI 自定义节点仓库；安装后需要重启 ComfyUI。",
    }


@app.post("/api/install")
async def install_workflow_assets() -> dict[str, Any]:
    assets = collect_asset_checks()
    custom_nodes = collect_custom_node_checks()
    missing = installable_missing_items(assets, custom_nodes)
    job_id = uuid.uuid4().hex
    job = {
        "id": job_id,
        "status": "queued",
        "completed": False,
        "created_at": now_ms(),
        "missing": missing,
        "log": [],
    }
    INSTALL_JOBS[job_id] = job

    if not missing:
        job["status"] = "success"
        job["completed"] = True
        job["finished_at"] = now_ms()
        job["skipped"] = True
        job["log"].append("所有可一键安装的模型、权重和自定义节点都已存在，无需下载。")
        return job

    script = WORKSPACE_DIR / "scripts" / "install_workflow_assets.py"
    if not script.exists():
        job["status"] = "failed"
        job["completed"] = True
        job["finished_at"] = now_ms()
        job["log"].append(f"安装脚本不存在：{script}")
        return job

    command = [sys.executable, "-u", str(script), "--base-dir", str(BASE_DIR)]
    job["command"] = install_command_text(command)
    thread = threading.Thread(target=run_install_worker, args=(job_id, command), daemon=True)
    thread.start()
    return job


@app.get("/api/install/{job_id}")
async def get_install_job(job_id: str) -> dict[str, Any]:
    job = INSTALL_JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="安装任务不存在")
    return job


@app.get("/api/validate")
async def validate() -> dict[str, Any]:
    required_nodes = [
        "SaveVideo",
        "CreateVideo",
        "LoadImage",
        "UNETLoader",
        "CLIPLoader",
        "VAELoader",
        "CLIPTextEncode",
        "WanImageToVideo",
        "Wan22ImageToVideoLatent",
        "EmptyHunyuanLatentVideo",
        "KSampler",
        "KSamplerAdvanced",
        "VAEDecodeTiled",
        "LoraLoaderModelOnly",
        "ModelSamplingSD3",
        "VHS_LoadVideo",
        "VHS_VideoCombine",
        "RIFE VFI",
        "UpscaleModelLoader",
        "ImageUpscaleWithModel",
    ]
    object_info = await comfy_get("/object_info")
    node_checks = [{"name": name, "ok": name in object_info} for name in required_nodes]

    required_files = [
        BASE_DIR / "models" / "diffusion_models" / "Wan2.2" / "wan2.2_ti2v_5B_fp16.safetensors",
        BASE_DIR / "models" / "diffusion_models" / "Wan2.2" / "wan2.2_i2v_high_noise_14B_fp8_scaled.safetensors",
        BASE_DIR / "models" / "diffusion_models" / "Wan2.2" / "wan2.2_i2v_low_noise_14B_fp8_scaled.safetensors",
        BASE_DIR / "models" / "diffusion_models" / "Wan2.2" / "wan2.2_t2v_high_noise_14B_fp8_scaled.safetensors",
        BASE_DIR / "models" / "diffusion_models" / "Wan2.2" / "wan2.2_t2v_low_noise_14B_fp8_scaled.safetensors",
        BASE_DIR / "models" / "text_encoders" / "umt5_xxl_fp8_e4m3fn_scaled.safetensors",
        BASE_DIR / "models" / "vae" / "wan_2.1_vae.safetensors",
        BASE_DIR / "models" / "vae" / "wan2.2_vae.safetensors",
        BASE_DIR / "models" / "loras" / "Wan2.2" / "wan2.2_i2v_lightx2v_4steps_lora_v1_high_noise.safetensors",
        BASE_DIR / "models" / "loras" / "Wan2.2" / "wan2.2_i2v_lightx2v_4steps_lora_v1_low_noise.safetensors",
        BASE_DIR / "models" / "loras" / "Wan2.2" / "wan2.2_t2v_lightx2v_4steps_lora_v1.1_high_noise.safetensors",
        BASE_DIR / "models" / "loras" / "Wan2.2" / "wan2.2_t2v_lightx2v_4steps_lora_v1.1_low_noise.safetensors",
        BASE_DIR / "custom_nodes" / "ComfyUI-Frame-Interpolation" / "ckpts" / "rife" / "rife49.pth",
        BASE_DIR / "models" / "upscale_models" / "RealESRGAN_x2plus.pth",
        BASE_DIR / "models" / "upscale_models" / "4x-UltraSharp.pth",
        INPUT_DIR / DEFAULT_IMAGE,
    ]
    file_checks = [
        {
            "path": str(path),
            "ok": path.exists(),
            "size_gb": round(path.stat().st_size / 1024**3, 2) if path.exists() else 0,
        }
        for path in required_files
    ]

    graph_checks: list[dict[str, Any]] = []
    sample_params = {
        "prompt": DEFAULT_PROMPT,
        "negative": DEFAULT_NEGATIVE,
        "image_name": DEFAULT_IMAGE,
        "width": 1280,
        "height": 704,
        "length": 81,
        "fps": 24,
        "seed": 1,
        "steps": 4,
        "cfg": 1.0,
    }
    graphs = {
        "I2V A14B": build_a14b_i2v_prompt(**sample_params),
        "T2V A14B": build_a14b_t2v_prompt(
            **{k: v for k, v in sample_params.items() if k != "image_name"}
        ),
        "TI2V 5B": build_ti2v_prompt(
            **{**sample_params, "steps": 20, "cfg": 5.0}
        ),
        "RIFE 2x": build_rife_prompt(video_name="sample.mp4", fps=24, multiplier=2),
        "Upscale 2x": build_video_upscale_prompt(video_name="sample.mp4", fps=24),
    }
    for name, graph in graphs.items():
        errors = validate_graph(graph)
        graph_checks.append({"name": name, "ok": not errors, "errors": errors})

    try:
        ffmpeg_ok = bool(find_ffmpeg())
    except HTTPException:
        ffmpeg_ok = False

    tool_checks = [
        {"name": "ffmpeg", "ok": ffmpeg_ok},
        {"name": "ffmpeg:deflicker", "ok": ffmpeg_has_filter("deflicker")},
        {"name": "ffmpeg:hqdn3d", "ok": ffmpeg_has_filter("hqdn3d")},
    ]

    ok = all(item["ok"] for item in node_checks + file_checks + graph_checks + tool_checks)
    return {
        "ok": ok,
        "nodes": node_checks,
        "files": file_checks,
        "graphs": graph_checks,
        "tools": tool_checks,
    }


@app.get("/api/workflow")
async def workflow_preview(
    mode: str = "",
    model_profile: str = "",
    model_label: str = "",
    width: int = 1280,
    height: int = 704,
    length: int = 81,
    fps: int = 24,
    seed: str = "1",
    steps: int = 4,
    cfg: float = 1.0,
    upscale_model: str = "RealESRGAN_x2plus.pth",
    rife_multiplier: int = 2,
    include_graph: bool = False,
) -> dict[str, Any]:
    width = normalize_dimension(width)
    height = normalize_dimension(height)
    length = normalize_frames(length)
    fps = clamp_int(fps, 1, 60)
    seed_value = normalize_seed(seed)
    steps = clamp_int(steps, 1, 80)
    cfg = clamp_float(cfg, 0.1, 20.0)
    upscale_model = upscale_model if upscale_model in {"RealESRGAN_x2plus.pth", "4x-UltraSharp.pth"} else "RealESRGAN_x2plus.pth"
    rife_multiplier = clamp_int(rife_multiplier, 2, 4)
    resolved = resolve_workflow_profile(
        model_profile=model_profile,
        mode=mode,
        model_label=model_label,
        upscale_model=upscale_model,
        rife_multiplier=rife_multiplier,
    )
    mode = resolved["mode"]
    upscale_model = resolved["upscale_model"]
    rife_multiplier = resolved["rife_multiplier"]

    if mode == "":
        return {
            "ok": True,
            "mode": mode,
            "profile": resolved["profile"],
            "label": resolved["label"],
            "local": False,
            "node_count": 0,
            "class_types": [],
            "output_nodes": [],
            "errors": [],
            "message": "当前步骤不需要创建 ComfyUI 视频工作流。",
        }

    if mode == "deflicker":
        try:
            ffmpeg_path = find_ffmpeg()
            filters_ok = ffmpeg_has_filter("deflicker") and ffmpeg_has_filter("hqdn3d")
        except HTTPException:
            ffmpeg_path = ""
            filters_ok = False
        return {
            "ok": bool(filters_ok),
            "mode": mode,
            "profile": resolved["profile"],
            "label": resolved["label"] or "ffmpeg deflicker + hqdn3d",
            "local": True,
            "node_count": 0,
            "class_types": [],
            "output_nodes": [],
            "errors": [] if filters_ok else ["缺少 ffmpeg 或 deflicker/hqdn3d 滤镜"],
            "tool": {"ffmpeg": ffmpeg_path, "filters_ok": filters_ok},
            "message": "本步骤使用本地 ffmpeg，不需要 ComfyUI 图。" if filters_ok else "本地 ffmpeg 预检未通过。",
        }

    graph = build_preview_workflow_graph(
        mode=mode,
        prompt=text_or_default("", DEFAULT_PROMPT),
        negative=text_or_default("", DEFAULT_NEGATIVE),
        width=width,
        height=height,
        length=length,
        fps=fps,
        seed=seed_value,
        steps=steps,
        cfg=cfg,
        upscale_model=upscale_model,
        rife_multiplier=rife_multiplier,
    )
    if graph is None:
        raise HTTPException(status_code=400, detail="未知模式，无法创建 ComfyUI 工作流")

    graph_errors = validate_graph(graph)
    availability = await comfy_node_availability(graph)
    output_nodes = [
        node_id
        for node_id, item in graph.items()
        if item.get("class_type") in {"SaveVideo", "VHS_VideoCombine", "SaveImage"}
    ]
    errors = list(graph_errors)
    if availability["missing"]:
        errors.append(f"ComfyUI 缺少节点：{', '.join(availability['missing'])}")
    if availability["error"]:
        errors.append(availability["error"])
    response: dict[str, Any] = {
        "ok": not errors,
        "mode": mode,
        "profile": resolved["profile"],
        "label": resolved["label"],
        "local": False,
        "node_count": len(graph),
        "class_types": graph_class_types(graph),
        "output_nodes": output_nodes,
        "errors": errors,
        "comfy": availability,
        "parameters": {
            "width": width,
            "height": height,
            "length": length,
            "fps": fps,
            "steps": steps,
            "cfg": cfg,
            "upscale_model": upscale_model,
            "rife_multiplier": rife_multiplier,
        },
        "message": "ComfyUI workflow 已创建并预检通过。" if not errors else "ComfyUI workflow 预检未通过。",
    }
    if include_graph:
        response["graph"] = graph
    return response


@app.post("/api/generate")
async def generate(
    mode: str = Form(...),
    prompt: str = Form(""),
    negative: str = Form(""),
    width: int = Form(1280),
    height: int = Form(704),
    length: int = Form(81),
    fps: int = Form(24),
    seed: str = Form("-1"),
    steps: int = Form(4),
    cfg: float = Form(1.0),
    model_label: str = Form(""),
    model_profile: str = Form(""),
    upscale_model: str = Form("RealESRGAN_x2plus.pth"),
    rife_multiplier: int = Form(2),
    source_video_filename: str = Form(""),
    source_video_subfolder: str = Form(""),
    source_video_type: str = Form(""),
    image: UploadFile | None = File(None),
    video: UploadFile | None = File(None),
) -> JSONResponse:
    prompt_text = text_or_default(prompt, DEFAULT_PROMPT)
    negative_text = text_or_default(negative, DEFAULT_NEGATIVE)
    width = normalize_dimension(width)
    height = normalize_dimension(height)
    length = normalize_frames(length)
    fps = clamp_int(fps, 1, 60)
    seed_value = normalize_seed(seed)
    steps = clamp_int(steps, 1, 80)
    cfg = clamp_float(cfg, 0.1, 20.0)
    selected_label = clean_label(model_label)
    upscale_model = upscale_model if upscale_model in {"RealESRGAN_x2plus.pth", "4x-UltraSharp.pth"} else "RealESRGAN_x2plus.pth"
    rife_multiplier = clamp_int(rife_multiplier, 2, 4)
    resolved = resolve_workflow_profile(
        model_profile=model_profile,
        mode=mode,
        model_label=selected_label,
        upscale_model=upscale_model,
        rife_multiplier=rife_multiplier,
    )
    mode = resolved["mode"]
    selected_label = resolved["label"]
    upscale_model = resolved["upscale_model"]
    rife_multiplier = clamp_int(resolved["rife_multiplier"], 2, 4)

    if mode in {"i2v_a14b", "ti2v_5b"}:
        image_name = DEFAULT_IMAGE
        if image and image.filename:
            image_name = await save_upload(image, IMAGE_EXTENSIONS)
    else:
        image_name = ""

    if mode == "i2v_a14b":
        graph = build_a14b_i2v_prompt(
            prompt=prompt_text,
            negative=negative_text,
            image_name=image_name,
            width=width,
            height=height,
            length=length,
            fps=fps,
            seed=seed_value,
            steps=steps,
            cfg=cfg,
        )
        title = selected_label or "I2V A14B 正片"
    elif mode == "t2v_a14b":
        graph = build_a14b_t2v_prompt(
            prompt=prompt_text,
            negative=negative_text,
            width=width,
            height=height,
            length=length,
            fps=fps,
            seed=seed_value,
            steps=steps,
            cfg=cfg,
        )
        title = selected_label or "T2V A14B 文生视频"
    elif mode == "ti2v_5b":
        graph = build_ti2v_prompt(
            prompt=prompt_text,
            negative=negative_text,
            image_name=image_name,
            width=width,
            height=height,
            length=length,
            fps=fps,
            seed=seed_value,
            steps=steps,
            cfg=cfg,
        )
        title = selected_label or "TI2V 5B 草稿"
    elif mode == "rife_2x":
        if source_video_filename:
            video_name = copy_media_to_input(
                source_video_filename,
                source_video_subfolder,
                source_video_type or "output",
            )
        elif video and video.filename:
            video_name = await save_upload(video, VIDEO_EXTENSIONS)
        else:
            raise HTTPException(status_code=400, detail="RIFE 模式需要上传视频，或先完成上一阶段视频生成")
        graph = build_rife_prompt(video_name=video_name, fps=fps, multiplier=rife_multiplier)
        title = selected_label or f"RIFE {rife_multiplier}x 插帧"
    elif mode == "deflicker":
        if source_video_filename:
            source_path = safe_media_path(
                source_video_filename,
                source_video_subfolder,
                source_video_type or "output",
            )
        elif video and video.filename:
            video_name = await save_upload(video, VIDEO_EXTENSIONS)
            source_path = input_media_path(video_name)
        else:
            raise HTTPException(status_code=400, detail="闪烁修复需要上传视频，或先完成上一阶段视频生成")

        media = run_deflicker(source_path)
        job_id = uuid.uuid4().hex
        JOBS[job_id] = {
            "id": job_id,
            "prompt_id": None,
            "mode": mode,
            "title": selected_label or "画面闪烁修复",
            "status": "success",
            "completed": True,
            "local": True,
            "created_at": now_ms(),
            "finished_at": now_ms(),
            "seed": seed_value,
            "width": width,
            "height": height,
            "length": length,
            "fps": fps,
            "media": [media],
            "raw": {"processor": "ffmpeg", "filters": "deflicker,hqdn3d"},
            "model_profile": resolved["profile"],
        }
        return JSONResponse(JOBS[job_id])
    elif mode == "upscale_2x":
        if source_video_filename:
            video_name = copy_media_to_input(
                source_video_filename,
                source_video_subfolder,
                source_video_type or "output",
            )
        elif video and video.filename:
            video_name = await save_upload(video, VIDEO_EXTENSIONS)
        else:
            raise HTTPException(status_code=400, detail="清晰度增强需要上传视频，或先完成上一阶段视频生成")
        graph = build_video_upscale_prompt(video_name=video_name, fps=fps, model_name=upscale_model)
        title = selected_label or (
            "4x-UltraSharp 清晰度增强"
            if upscale_model == "4x-UltraSharp.pth"
            else "RealESRGAN 2x 清晰度增强"
        )
    else:
        raise HTTPException(status_code=400, detail="未知模式")

    graph_errors = validate_graph(graph)
    if graph_errors:
        raise HTTPException(status_code=400, detail={"message": "工作流图无效", "errors": graph_errors})

    response = await comfy_post("/prompt", {"prompt": graph, "client_id": CLIENT_ID})
    prompt_id = response.get("prompt_id")
    if not prompt_id:
        raise HTTPException(status_code=500, detail=response)

    job_id = uuid.uuid4().hex
    JOBS[job_id] = {
        "id": job_id,
        "prompt_id": prompt_id,
        "mode": mode,
        "title": title,
        "status": "queued",
        "created_at": now_ms(),
        "seed": seed_value,
        "width": width,
        "height": height,
        "length": length,
        "fps": fps,
        "media": [],
        "raw": response,
        "model_profile": resolved["profile"],
    }
    return JSONResponse(JOBS[job_id])


@app.get("/api/jobs/{job_id}")
async def get_job(job_id: str) -> dict[str, Any]:
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="任务不存在")
    if job.get("local"):
        return job

    history = await comfy_get(f"/history/{job['prompt_id']}")
    history_item = history.get(job["prompt_id"])
    if history_item:
        status_info = history_item.get("status", {})
        status_text = status_info.get("status_str", "success")
        job["status"] = "success" if status_text == "success" else status_text
        job["completed"] = bool(status_info.get("completed", job["status"] == "success"))
        job["media"] = extract_media(history_item)
        job["messages"] = status_info.get("messages", [])
        job["finished_at"] = job.get("finished_at") or now_ms()
    else:
        queue = await comfy_get("/queue")
        queue_items = (queue.get("queue_running") or []) + (queue.get("queue_pending") or [])
        prompt_ids = {str(item[1]) for item in queue_items if isinstance(item, list) and len(item) > 1}
        job["status"] = "running" if job["prompt_id"] in prompt_ids else "queued"

    return job


@app.get("/api/view")
async def view(filename: str, subfolder: str = "", type: str = "output") -> FileResponse:
    path = safe_media_path(filename, subfolder, type)
    media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return FileResponse(path, media_type=media_type, filename=path.name)


@app.exception_handler(HTTPException)
async def http_exception_handler(_: Any, exc: HTTPException) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
