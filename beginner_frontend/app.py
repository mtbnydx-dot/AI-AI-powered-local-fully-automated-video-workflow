from __future__ import annotations

import mimetypes
import json
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
from urllib.parse import quote, urlparse

import httpx
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles


APP_DIR = Path(__file__).resolve().parent
WORKSPACE_DIR = APP_DIR.parent


def looks_like_comfy_base(path: Path) -> bool:
    return all((path / name).exists() for name in ("models", "input", "output", "custom_nodes"))


def default_comfy_base_dir() -> Path:
    configured = os.environ.get("COMFY_BASE_DIR")
    if configured:
        return Path(configured).expanduser().resolve()
    parent = WORKSPACE_DIR.parent
    if looks_like_comfy_base(parent):
        return parent.resolve()
    return WORKSPACE_DIR.resolve()


BASE_DIR = default_comfy_base_dir()
INPUT_DIR = BASE_DIR / "input"
OUTPUT_DIR = BASE_DIR / "output"
TEMP_DIR = BASE_DIR / "temp"
UPLOAD_DIR = INPUT_DIR / "beginner_frontend"
STATIC_DIR = APP_DIR / "static"

COMFY_URL = os.environ.get("COMFY_URL", "http://127.0.0.1:8000").rstrip("/")
CLIENT_ID = f"beginner-frontend-{uuid.uuid4()}"
COMFY_INSTALL_DIR = Path(os.environ.get("COMFY_INSTALL_DIR", BASE_DIR / "ComfyUI")).expanduser().resolve()

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
COMFY_PROCESS: subprocess.Popen[str] | None = None


app = FastAPI(title="Wan2.2 Beginner Frontend")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


def configured_comfy_paths() -> dict[str, Any]:
    return {
        "base_dir": BASE_DIR,
        "input_dir": INPUT_DIR,
        "output_dir": OUTPUT_DIR,
        "temp_dir": TEMP_DIR,
        "user_dir": BASE_DIR / "user",
        "source": "configured",
    }


def path_from_arg(argv: list[Any], flag: str) -> str | None:
    for index, item in enumerate(argv):
        value = str(item)
        if value == flag and index + 1 < len(argv):
            return str(argv[index + 1])
        prefix = f"{flag}="
        if value.startswith(prefix):
            return value[len(prefix) :]
    return None


def comfy_paths_from_system_stats(system_stats: dict[str, Any] | None) -> dict[str, Any]:
    paths = configured_comfy_paths()
    argv = ((system_stats or {}).get("system") or {}).get("argv") or []
    source = "configured"

    base_arg = path_from_arg(argv, "--base-directory")
    if base_arg:
        paths["base_dir"] = Path(base_arg).expanduser().resolve()
        source = "running_comfyui"

    base_dir = Path(paths["base_dir"])
    for key, flag, fallback in [
        ("input_dir", "--input-directory", base_dir / "input"),
        ("output_dir", "--output-directory", base_dir / "output"),
        ("temp_dir", "--temp-directory", base_dir / "temp"),
        ("user_dir", "--user-directory", base_dir / "user"),
    ]:
        arg = path_from_arg(argv, flag)
        paths[key] = Path(arg).expanduser().resolve() if arg else fallback

    paths["source"] = source
    paths["base_dir_mismatch"] = Path(paths["base_dir"]).resolve() != BASE_DIR.resolve()
    return paths


def serializable_comfy_paths(paths: dict[str, Any]) -> dict[str, Any]:
    return {
        "configured_base_dir": str(BASE_DIR),
        "active_base_dir": str(paths["base_dir"]),
        "input_dir": str(paths["input_dir"]),
        "output_dir": str(paths["output_dir"]),
        "temp_dir": str(paths["temp_dir"]),
        "user_dir": str(paths["user_dir"]),
        "source": paths.get("source", "configured"),
        "base_dir_mismatch": bool(paths.get("base_dir_mismatch")),
    }


async def active_comfy_paths() -> dict[str, Any]:
    try:
        system_stats = await comfy_get("/system_stats")
    except HTTPException:
        return configured_comfy_paths()
    return comfy_paths_from_system_stats(system_stats)


def upload_dir_for(paths: dict[str, Any] | None = None) -> Path:
    return Path((paths or configured_comfy_paths())["input_dir"]) / "beginner_frontend"


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


def normalize_ltx_frames(length: int) -> int:
    length = clamp_int(length, 9, 241)
    # LTXV latent video nodes use 1 + 8n frame counts.
    return 1 + 8 * round((length - 1) / 8)


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
        "mac_ltx_low_i2v": {
            "mode": "ltx_i2v",
            "label": "Mac LTX I2V 低档 384P",
        },
        "mac_ltx_balanced_i2v": {
            "mode": "ltx_i2v",
            "label": "Mac LTX I2V 均衡 512P",
        },
        "mac_ltx_quality_i2v": {
            "mode": "ltx_i2v",
            "label": "Mac LTX I2V 质量档",
        },
        "mac_ltx_low_t2v": {
            "mode": "ltx_t2v",
            "label": "Mac LTX T2V 低档",
        },
        "mac_ltx_balanced_t2v": {
            "mode": "ltx_t2v",
            "label": "Mac LTX T2V 均衡档",
        },
        "mac_wan5b_480p": {
            "mode": "ti2v_5b",
            "label": "Mac Wan2.2 TI2V-5B 480P 实验档",
        },
        "mac_wan5b_720p_experimental": {
            "mode": "ti2v_5b",
            "label": "Mac Wan2.2 TI2V-5B 720P 高内存实验档",
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


async def save_upload(upload: UploadFile, allowed: set[str], paths: dict[str, Any] | None = None) -> str:
    suffix = Path(upload.filename or "").suffix.lower()
    if suffix not in allowed:
        allowed_text = ", ".join(sorted(allowed))
        raise HTTPException(status_code=400, detail=f"文件格式不支持，请使用：{allowed_text}")

    upload_dir = upload_dir_for(paths)
    upload_dir.mkdir(parents=True, exist_ok=True)
    filename = safe_filename(upload.filename or f"upload{suffix}")
    destination = upload_dir / filename
    with destination.open("wb") as output:
        shutil.copyfileobj(upload.file, output)
    return f"beginner_frontend/{filename}"


def copy_media_to_input(filename: str, subfolder: str, file_type: str, paths: dict[str, Any] | None = None) -> str:
    source = safe_media_path(filename, subfolder, file_type, paths)
    if source.suffix.lower() not in VIDEO_EXTENSIONS:
        raise HTTPException(status_code=400, detail="上一阶段输出不是可处理的视频")

    upload_dir = upload_dir_for(paths)
    upload_dir.mkdir(parents=True, exist_ok=True)
    destination_name = safe_filename(source.name)
    destination = upload_dir / destination_name
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
                "unet_name": "Wan2.2/wan2.2_ti2v_5B_fp16.safetensors",
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


def build_ltx_t2v_prompt(
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
    return {
        "1": node("CheckpointLoaderSimple", {"ckpt_name": "ltx-video-2b-v0.9.5.safetensors"}),
        "2": node(
            "CLIPLoader",
            {
                "clip_name": "t5xxl_fp16.safetensors",
                "type": "ltxv",
                "device": "default",
            },
        ),
        "3": node("CLIPTextEncode", {"text": prompt, "clip": ["2", 0]}),
        "4": node("CLIPTextEncode", {"text": negative, "clip": ["2", 0]}),
        "5": node("LTXVConditioning", {"frame_rate": float(fps), "positive": ["3", 0], "negative": ["4", 0]}),
        "6": node(
            "EmptyLTXVLatentVideo",
            {
                "width": width,
                "height": height,
                "length": normalize_ltx_frames(length),
                "batch_size": 1,
            },
        ),
        "7": node(
            "LTXVScheduler",
            {
                "steps": steps,
                "max_shift": 2.05,
                "base_shift": 0.95,
                "stretch": True,
                "terminal": 0.1,
                "latent": ["6", 0],
            },
        ),
        "8": node("KSamplerSelect", {"sampler_name": "res_multistep"}),
        "9": node(
            "SamplerCustom",
            {
                "model": ["1", 0],
                "add_noise": True,
                "noise_seed": seed,
                "cfg": cfg,
                "positive": ["5", 0],
                "negative": ["5", 1],
                "sampler": ["8", 0],
                "sigmas": ["7", 0],
                "latent_image": ["6", 0],
            },
        ),
        "10": node("VAEDecode", {"samples": ["9", 0], "vae": ["1", 2]}),
        "11": node("CreateVideo", {"images": ["10", 0], "fps": float(fps)}),
        "12": node(
            "SaveVideo",
            {
                "video": ["11", 0],
                "filename_prefix": "wan22_frontend/Mac_LTX_T2V_%date:yyyy-MM-dd%",
                "format": "mp4",
                "codec": "h264",
            },
        ),
    }


def build_ltx_i2v_prompt(
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
        "2": node("LTXVPreprocess", {"image": ["1", 0], "img_compression": 40}),
        "3": node("CheckpointLoaderSimple", {"ckpt_name": "ltx-video-2b-v0.9.5.safetensors"}),
        "4": node(
            "CLIPLoader",
            {
                "clip_name": "t5xxl_fp16.safetensors",
                "type": "ltxv",
                "device": "default",
            },
        ),
        "5": node("CLIPTextEncode", {"text": prompt, "clip": ["4", 0]}),
        "6": node("CLIPTextEncode", {"text": negative, "clip": ["4", 0]}),
        "7": node(
            "LTXVImgToVideo",
            {
                "positive": ["5", 0],
                "negative": ["6", 0],
                "vae": ["3", 2],
                "image": ["2", 0],
                "width": width,
                "height": height,
                "length": normalize_ltx_frames(length),
                "batch_size": 1,
                "strength": 1.0,
            },
        ),
        "8": node("LTXVConditioning", {"frame_rate": float(fps), "positive": ["7", 0], "negative": ["7", 1]}),
        "9": node(
            "LTXVScheduler",
            {
                "steps": steps,
                "max_shift": 2.05,
                "base_shift": 0.95,
                "stretch": True,
                "terminal": 0.1,
                "latent": ["7", 2],
            },
        ),
        "10": node("KSamplerSelect", {"sampler_name": "euler"}),
        "11": node(
            "SamplerCustom",
            {
                "model": ["3", 0],
                "add_noise": True,
                "noise_seed": seed,
                "cfg": cfg,
                "positive": ["8", 0],
                "negative": ["8", 1],
                "sampler": ["10", 0],
                "sigmas": ["9", 0],
                "latent_image": ["7", 2],
            },
        ),
        "12": node("VAEDecode", {"samples": ["11", 0], "vae": ["3", 2]}),
        "13": node("CreateVideo", {"images": ["12", 0], "fps": float(fps)}),
        "14": node(
            "SaveVideo",
            {
                "video": ["13", 0],
                "filename_prefix": "wan22_frontend/Mac_LTX_I2V_%date:yyyy-MM-dd%",
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
                "unet_name": "Wan2.2/wan2.2_i2v_high_noise_14B_fp8_scaled.safetensors",
                "weight_dtype": "default",
            },
        ),
        "31": node(
            "LoraLoaderModelOnly",
            {
                "model": ["30", 0],
                "lora_name": "Wan2.2/wan2.2_i2v_lightx2v_4steps_lora_v1_high_noise.safetensors",
                "strength_model": 1.0,
            },
        ),
        "32": node("ModelSamplingSD3", {"model": ["31", 0], "shift": 5.0}),
        "40": node(
            "UNETLoader",
            {
                "unet_name": "Wan2.2/wan2.2_i2v_low_noise_14B_fp8_scaled.safetensors",
                "weight_dtype": "default",
            },
        ),
        "41": node(
            "LoraLoaderModelOnly",
            {
                "model": ["40", 0],
                "lora_name": "Wan2.2/wan2.2_i2v_lightx2v_4steps_lora_v1_low_noise.safetensors",
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
                "unet_name": "Wan2.2/wan2.2_t2v_high_noise_14B_fp8_scaled.safetensors",
                "weight_dtype": "default",
            },
        ),
        "31": node(
            "LoraLoaderModelOnly",
            {
                "model": ["30", 0],
                "lora_name": "Wan2.2/wan2.2_t2v_lightx2v_4steps_lora_v1.1_high_noise.safetensors",
                "strength_model": 1.0,
            },
        ),
        "32": node("ModelSamplingSD3", {"model": ["31", 0], "shift": 5.0}),
        "40": node(
            "UNETLoader",
            {
                "unet_name": "Wan2.2/wan2.2_t2v_low_noise_14B_fp8_scaled.safetensors",
                "weight_dtype": "default",
            },
        ),
        "41": node(
            "LoraLoaderModelOnly",
            {
                "model": ["40", 0],
                "lora_name": "Wan2.2/wan2.2_t2v_lightx2v_4steps_lora_v1.1_low_noise.safetensors",
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
    if mode == "ltx_i2v":
        return build_ltx_i2v_prompt(
            prompt=prompt,
            negative=negative,
            image_name=DEFAULT_IMAGE,
            width=width,
            height=height,
            length=normalize_ltx_frames(length),
            fps=fps,
            seed=seed,
            steps=steps,
            cfg=cfg,
        )
    if mode == "ltx_t2v":
        return build_ltx_t2v_prompt(
            prompt=prompt,
            negative=negative,
            width=width,
            height=height,
            length=normalize_ltx_frames(length),
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


def safe_media_path(filename: str, subfolder: str, file_type: str, paths: dict[str, Any] | None = None) -> Path:
    paths = paths or configured_comfy_paths()
    roots = {
        "input": Path(paths["input_dir"]),
        "output": Path(paths["output_dir"]),
        "temp": Path(paths["temp_dir"]),
    }
    root = roots.get(file_type)
    if root is None:
        raise HTTPException(status_code=400, detail="未知文件类型")

    base = root.resolve()
    candidate = (root / subfolder / filename).resolve()
    try:
        candidate.relative_to(base)
    except ValueError:
        raise HTTPException(status_code=400, detail="文件路径不安全")
    if not candidate.exists():
        raise HTTPException(status_code=404, detail="文件不存在")
    return candidate


def input_media_path(relative_name: str, paths: dict[str, Any] | None = None) -> Path:
    paths = paths or configured_comfy_paths()
    normalized = relative_name.replace("\\", "/")
    relative = Path(normalized)
    if relative.is_absolute() or ".." in relative.parts:
        raise HTTPException(status_code=400, detail="输入文件路径不安全")

    input_dir = Path(paths["input_dir"])
    base = input_dir.resolve()
    candidate = (input_dir / relative).resolve()
    try:
        candidate.relative_to(base)
    except ValueError:
        raise HTTPException(status_code=400, detail="输入文件路径不安全")
    if not candidate.exists():
        raise HTTPException(status_code=404, detail="输入文件不存在")
    return candidate


def output_media_item(path: Path, paths: dict[str, Any] | None = None) -> dict[str, Any]:
    paths = paths or configured_comfy_paths()
    output_base = Path(paths["output_dir"]).resolve()
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


def workflow_asset_manifest(base_dir: Path | None = None) -> list[dict[str, Any]]:
    base_dir = (base_dir or BASE_DIR).expanduser().resolve()
    input_dir = base_dir / "input"
    return [
        {
            "id": "ti2v_5b",
            "label": "Wan2.2 TI2V-5B fp16",
            "step": "试镜头",
            "path": base_dir / "models" / "diffusion_models" / "Wan2.2" / "wan2.2_ti2v_5B_fp16.safetensors",
            "bytes": 9999658848,
            "installable": True,
        },
        {
            "id": "i2v_high",
            "label": "Wan2.2 I2V-A14B high noise fp8",
            "step": "正式片段",
            "path": base_dir / "models" / "diffusion_models" / "Wan2.2" / "wan2.2_i2v_high_noise_14B_fp8_scaled.safetensors",
            "bytes": 14294742832,
            "installable": True,
        },
        {
            "id": "i2v_low",
            "label": "Wan2.2 I2V-A14B low noise fp8",
            "step": "正式片段",
            "path": base_dir / "models" / "diffusion_models" / "Wan2.2" / "wan2.2_i2v_low_noise_14B_fp8_scaled.safetensors",
            "bytes": 14294742832,
            "installable": True,
        },
        {
            "id": "t2v_high",
            "label": "Wan2.2 T2V-A14B high noise fp8",
            "step": "文字生视频",
            "path": base_dir / "models" / "diffusion_models" / "Wan2.2" / "wan2.2_t2v_high_noise_14B_fp8_scaled.safetensors",
            "bytes": 14293923632,
            "installable": True,
        },
        {
            "id": "t2v_low",
            "label": "Wan2.2 T2V-A14B low noise fp8",
            "step": "文字生视频",
            "path": base_dir / "models" / "diffusion_models" / "Wan2.2" / "wan2.2_t2v_low_noise_14B_fp8_scaled.safetensors",
            "bytes": 14293923632,
            "installable": True,
        },
        {
            "id": "umt5",
            "label": "UMT5 XXL fp8 文本编码器",
            "step": "视频生成",
            "path": base_dir / "models" / "text_encoders" / "umt5_xxl_fp8_e4m3fn_scaled.safetensors",
            "bytes": 6735906897,
            "installable": True,
        },
        {
            "id": "wan21_vae",
            "label": "Wan 2.1 VAE",
            "step": "A14B 正片",
            "path": base_dir / "models" / "vae" / "wan_2.1_vae.safetensors",
            "bytes": 253815318,
            "installable": True,
        },
        {
            "id": "wan22_vae",
            "label": "Wan 2.2 VAE",
            "step": "TI2V 草稿",
            "path": base_dir / "models" / "vae" / "wan2.2_vae.safetensors",
            "bytes": 1409400960,
            "installable": True,
        },
        {
            "id": "ltx_2b_095",
            "label": "LTX-Video 2B 0.9.5",
            "step": "Mac LTX 视频",
            "path": base_dir / "models" / "checkpoints" / "ltx-video-2b-v0.9.5.safetensors",
            "bytes": 6340729500,
            "installable": True,
        },
        {
            "id": "t5xxl_fp16",
            "label": "T5 XXL fp16 文本编码器",
            "step": "Mac LTX 视频",
            "path": base_dir / "models" / "text_encoders" / "t5xxl_fp16.safetensors",
            "bytes": 9787841024,
            "installable": True,
        },
        {
            "id": "i2v_lora_high",
            "label": "I2V 4步 LoRA high noise",
            "step": "正式片段",
            "path": base_dir / "models" / "loras" / "Wan2.2" / "wan2.2_i2v_lightx2v_4steps_lora_v1_high_noise.safetensors",
            "bytes": 1226977424,
            "installable": True,
        },
        {
            "id": "i2v_lora_low",
            "label": "I2V 4步 LoRA low noise",
            "step": "正式片段",
            "path": base_dir / "models" / "loras" / "Wan2.2" / "wan2.2_i2v_lightx2v_4steps_lora_v1_low_noise.safetensors",
            "bytes": 1226977424,
            "installable": True,
        },
        {
            "id": "t2v_lora_high",
            "label": "T2V 4步 LoRA high noise",
            "step": "文字生视频",
            "path": base_dir / "models" / "loras" / "Wan2.2" / "wan2.2_t2v_lightx2v_4steps_lora_v1.1_high_noise.safetensors",
            "bytes": 1226977424,
            "installable": True,
        },
        {
            "id": "t2v_lora_low",
            "label": "T2V 4步 LoRA low noise",
            "step": "文字生视频",
            "path": base_dir / "models" / "loras" / "Wan2.2" / "wan2.2_t2v_lightx2v_4steps_lora_v1.1_low_noise.safetensors",
            "bytes": 1226977424,
            "installable": True,
        },
        {
            "id": "rife49",
            "label": "RIFE 4.9 插帧权重",
            "step": "RIFE 插帧",
            "path": base_dir / "custom_nodes" / "ComfyUI-Frame-Interpolation" / "ckpts" / "rife" / "rife49.pth",
            "bytes": 21345274,
            "installable": True,
        },
        {
            "id": "realesrgan_x2",
            "label": "RealESRGAN x2 视频超分权重",
            "step": "清晰度增强",
            "path": base_dir / "models" / "upscale_models" / "RealESRGAN_x2plus.pth",
            "bytes": 67061725,
            "installable": True,
        },
        {
            "id": "ultrasharp_x4",
            "label": "4x-UltraSharp 备用超分权重",
            "step": "清晰度增强",
            "path": base_dir / "models" / "upscale_models" / "4x-UltraSharp.pth",
            "bytes": 66961958,
            "installable": True,
        },
        {
            "id": "sample_keyframe",
            "label": "内置示例关键帧",
            "step": "关键帧",
            "path": input_dir / DEFAULT_IMAGE,
            "bytes": None,
            "installable": True,
        },
    ]


def custom_node_manifest(base_dir: Path | None = None) -> list[dict[str, Any]]:
    base_dir = (base_dir or BASE_DIR).expanduser().resolve()
    return [
        {
            "id": "video_helper_suite",
            "label": "ComfyUI-VideoHelperSuite",
            "path": base_dir / "custom_nodes" / "ComfyUI-VideoHelperSuite",
            "installable": True,
        },
        {
            "id": "frame_interpolation",
            "label": "ComfyUI-Frame-Interpolation",
            "path": base_dir / "custom_nodes" / "ComfyUI-Frame-Interpolation",
            "installable": True,
        },
    ]


def path_label(path: Path, base_dir: Path | None = None) -> str:
    base_dir = (base_dir or BASE_DIR).expanduser().resolve()
    try:
        return str(path.resolve().relative_to(base_dir)).replace("\\", "/")
    except ValueError:
        return str(path)


def collect_asset_checks(base_dir: Path | None = None) -> list[dict[str, Any]]:
    base_dir = (base_dir or BASE_DIR).expanduser().resolve()
    checks: list[dict[str, Any]] = []
    for item in workflow_asset_manifest(base_dir):
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
                "relative_path": path_label(path, base_dir),
                "ok": bool(size_ok),
                "exists": exists,
                "size_gb": round(size / 1024**3, 2) if exists else 0,
                "expected_gb": round(expected / 1024**3, 2) if expected else None,
                "installable": bool(item.get("installable")),
                "reason": reason,
            }
        )
    return checks


def collect_custom_node_checks(base_dir: Path | None = None) -> list[dict[str, Any]]:
    base_dir = (base_dir or BASE_DIR).expanduser().resolve()
    checks: list[dict[str, Any]] = []
    for item in custom_node_manifest(base_dir):
        path = item["path"]
        ok = path.exists() and path.is_dir()
        checks.append(
            {
                "id": item["id"],
                "label": item["label"],
                "path": str(path),
                "relative_path": path_label(path, base_dir),
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


def run_text_command(command: list[str], timeout: int = 8) -> str:
    result = subprocess.run(command, capture_output=True, text=True, check=True, timeout=timeout)
    return result.stdout.strip()


def mac_chip_tier(chip_name: str) -> str:
    value = chip_name.lower()
    if "ultra" in value:
        return "ultra"
    if "max" in value:
        return "max"
    if "pro" in value:
        return "pro"
    if value:
        return "base"
    return ""


def detect_mac_hardware() -> dict[str, Any]:
    info: dict[str, Any] = {
        "is_macos": platform.system() == "Darwin",
        "apple_silicon": False,
        "chip": "",
        "chip_tier": "",
        "unified_memory_gb": None,
        "machine_model": "",
        "machine_name": "",
        "mps_capable": False,
    }
    if not info["is_macos"]:
        return info

    machine = platform.machine()
    info["apple_silicon"] = machine == "arm64"
    try:
        info["chip"] = run_text_command(["sysctl", "-n", "machdep.cpu.brand_string"], timeout=5)
    except Exception:
        info["chip"] = platform.processor() or ""

    try:
        mem_bytes = int(run_text_command(["sysctl", "-n", "hw.memsize"], timeout=5))
        info["unified_memory_gb"] = round(mem_bytes / 1024**3, 1)
    except Exception:
        info["unified_memory_gb"] = get_system_memory_gb()

    try:
        raw = run_text_command(["system_profiler", "SPHardwareDataType", "-json"], timeout=12)
        payload = json.loads(raw)
        hardware_items = payload.get("SPHardwareDataType") or []
        if hardware_items:
            hardware = hardware_items[0]
            info["machine_model"] = hardware.get("machine_model", "") or ""
            info["machine_name"] = hardware.get("machine_name", "") or ""
            if hardware.get("chip_type"):
                info["chip"] = hardware.get("chip_type")
    except Exception:
        pass

    info["chip_tier"] = mac_chip_tier(str(info.get("chip") or ""))
    info["mps_capable"] = bool(info["apple_silicon"])
    return info


def probe_python_torch(python: Path) -> dict[str, Any]:
    probe: dict[str, Any] = {
        "python": str(python),
        "exists": python.exists(),
        "available": False,
        "cuda_available": False,
        "mps_built": False,
        "mps_available": False,
    }
    if not python.exists():
        return probe
    code = (
        "import json\n"
        "payload={'available': False, 'cuda_available': False, 'mps_built': False, 'mps_available': False}\n"
        "try:\n"
        " import torch\n"
        " payload['available']=True\n"
        " payload['version']=getattr(torch,'__version__',None)\n"
        " payload['cuda_available']=bool(torch.cuda.is_available())\n"
        " mps=getattr(getattr(torch,'backends',None),'mps',None)\n"
        " payload['mps_built']=bool(mps and mps.is_built())\n"
        " payload['mps_available']=bool(mps and mps.is_available())\n"
        "except Exception as exc:\n"
        " payload['error']=str(exc)\n"
        "print(json.dumps(payload))\n"
    )
    try:
        result = subprocess.run(
            [str(python), "-c", code],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        if result.stdout.strip():
            probe.update(json.loads(result.stdout.strip().splitlines()[-1]))
        if result.returncode != 0 and result.stderr.strip():
            probe["error"] = result.stderr.strip()[-1000:]
    except Exception as exc:
        probe["error"] = str(exc)
    return probe


def comfy_torch_probe() -> dict[str, Any]:
    try:
        return probe_python_torch(comfy_venv_python())
    except Exception as exc:
        return {
            "python": str(COMFY_INSTALL_DIR),
            "exists": False,
            "available": False,
            "cuda_available": False,
            "mps_built": False,
            "mps_available": False,
            "error": str(exc),
        }


def comfy_devices_have_mps(devices: list[dict[str, Any]]) -> bool:
    for device in devices:
        text = f"{device.get('type', '')} {device.get('name', '')}".lower()
        if any(token in text for token in ("mps", "metal", "apple")):
            return True
    return False


def mac_video_tier(mac: dict[str, Any]) -> str:
    if not mac.get("is_macos"):
        return ""
    if not mac.get("apple_silicon"):
        return "mac_post_only"

    memory = float(mac.get("unified_memory_gb") or 0)
    chip_tier = str(mac.get("chip_tier") or "base")
    if memory < 12:
        tier = "mac_ltx_low"
    elif memory < 24:
        tier = "mac_ltx_low"
    elif memory < 32:
        tier = "mac_ltx_balanced"
    elif memory < 48:
        tier = "mac_ltx_quality"
    elif memory < 96:
        tier = "mac_wan5b_480p"
    else:
        tier = "mac_wan5b_720p_experimental"

    if chip_tier == "base" and tier not in {"mac_ltx_low", "mac_ltx_balanced"}:
        return "mac_ltx_balanced"
    if chip_tier == "pro" and tier in {"mac_wan5b_480p", "mac_wan5b_720p_experimental"}:
        return "mac_ltx_quality"
    return tier


def platform_strategy_for(
    *,
    mac: dict[str, Any],
    has_cuda: bool,
    has_mps_capability: bool,
) -> str:
    if mac.get("is_macos"):
        if mac.get("apple_silicon") and has_mps_capability:
            return "mac_mps"
        return "mac_post_only"
    if has_cuda:
        return "cuda_wan_workflow"
    return "post_only"


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
    mac_info = detect_mac_hardware()
    comfy_torch = comfy_torch_probe()
    comfy_devices = summarize_comfy_devices(system_stats)
    devices = comfy_devices or torch_info.get("devices") or []
    max_vram = max((device.get("vram_total_gb") or 0 for device in devices), default=0)
    sum_vram = round(sum((device.get("vram_total_gb") or 0 for device in devices)), 1)
    has_cuda = bool(torch_info.get("cuda_available")) or any(
        "cuda" in str(device.get("type", "")).lower() or "nvidia" in str(device.get("name", "")).lower()
        for device in devices
    )
    comfy_reports_mps = comfy_devices_have_mps(comfy_devices)
    front_mps_ready = bool(torch_info.get("mps_available"))
    comfy_mps_ready = bool(comfy_torch.get("mps_available")) or comfy_reports_mps
    has_mps_capability = bool(mac_info.get("mps_capable") or front_mps_ready or comfy_mps_ready)
    has_mps = bool(front_mps_ready or comfy_mps_ready or mac_info.get("mps_capable"))
    accelerator = "cuda" if has_cuda else "mps" if has_mps else "cpu"
    platform_strategy = platform_strategy_for(
        mac=mac_info,
        has_cuda=has_cuda,
        has_mps_capability=has_mps_capability,
    )
    mac_tier = mac_video_tier(mac_info)
    return {
        "accelerator": accelerator,
        "platform_strategy": platform_strategy,
        "devices": devices,
        "gpu_count": len(devices),
        "max_vram_gb": round(max_vram, 1),
        "sum_vram_gb": sum_vram,
        "system_memory_gb": get_system_memory_gb(),
        "torch": torch_info,
        "front_torch_mps_ready": front_mps_ready,
        "comfy_torch": comfy_torch,
        "comfy_torch_mps_ready": comfy_mps_ready,
        "mac": mac_info,
        "mac_video_tier": mac_tier,
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
        "CheckpointLoaderSimple",
        "EmptyLTXVLatentVideo",
        "LTXVConditioning",
        "LTXVScheduler",
        "LTXVImgToVideo",
        "LTXVPreprocess",
        "SamplerCustom",
        "KSamplerSelect",
        "VAEDecode",
        "VHS_LoadVideo",
        "VHS_VideoCombine",
        "RIFE VFI",
        "UpscaleModelLoader",
        "ImageUpscaleWithModel",
    ]
    if object_info is None:
        return [{"name": name, "ok": False, "reason": "ComfyUI 未连接"} for name in required_nodes]
    return [{"name": name, "ok": name in object_info, "reason": "已加载" if name in object_info else "缺少"} for name in required_nodes]


def node_input_options(object_info: dict[str, Any] | None, node_name: str, input_name: str) -> list[str]:
    if not object_info or node_name not in object_info:
        return []
    node_info = object_info.get(node_name) or {}
    value = ((node_info.get("input") or {}).get("required") or {}).get(input_name)
    if value is None:
        value = ((node_info.get("input") or {}).get("optional") or {}).get(input_name)

    if isinstance(value, (list, tuple)):
        if value and isinstance(value[0], (list, tuple)):
            return [str(item) for item in value[0]]
        if len(value) > 1 and isinstance(value[1], dict) and isinstance(value[1].get("options"), (list, tuple)):
            return [str(item) for item in value[1]["options"]]
        if value and all(not isinstance(item, (dict, list, tuple)) for item in value):
            return [str(item) for item in value]
    if isinstance(value, dict) and isinstance(value.get("options"), (list, tuple)):
        return [str(item) for item in value["options"]]
    return []


def model_option_matches(options: list[str], expected: str) -> bool:
    expected_norm = expected.replace("\\", "/").lower()
    expected_base = Path(expected_norm).name
    for option in options:
        option_norm = option.replace("\\", "/").lower()
        if option_norm == expected_norm or option_norm.endswith(f"/{expected_norm}"):
            return True
        if Path(option_norm).name == expected_base:
            return True
    return False


def resolve_graph_model_option_names(graph: dict[str, Any], object_info: dict[str, Any]) -> dict[str, Any]:
    input_by_class = {
        "CheckpointLoaderSimple": "ckpt_name",
        "UNETLoader": "unet_name",
        "LoraLoaderModelOnly": "lora_name",
        "VAELoader": "vae_name",
        "CLIPLoader": "clip_name",
        "UpscaleModelLoader": "model_name",
    }
    for item in graph.values():
        class_type = item.get("class_type")
        input_name = input_by_class.get(str(class_type))
        if not input_name:
            continue
        inputs = item.get("inputs") or {}
        current = inputs.get(input_name)
        if not isinstance(current, str):
            continue
        options = node_input_options(object_info, str(class_type), input_name)
        for option in options:
            if model_option_matches([option], current):
                inputs[input_name] = option
                break
    return graph


def graph_model_registry_errors(graph: dict[str, Any], object_info: dict[str, Any] | None) -> list[str]:
    if object_info is None:
        return ["ComfyUI 未连接，无法验证 workflow 里的模型名。"]

    input_by_class = {
        "CheckpointLoaderSimple": "ckpt_name",
        "UNETLoader": "unet_name",
        "LoraLoaderModelOnly": "lora_name",
        "VAELoader": "vae_name",
        "CLIPLoader": "clip_name",
        "UpscaleModelLoader": "model_name",
    }
    errors: list[str] = []
    for node_id, item in graph.items():
        class_type = str(item.get("class_type") or "")
        input_name = input_by_class.get(class_type)
        if not input_name:
            continue
        inputs = item.get("inputs") or {}
        current = inputs.get(input_name)
        if not isinstance(current, str):
            continue
        options = node_input_options(object_info, class_type, input_name)
        if not model_option_matches(options, current):
            errors.append(f"{node_id}.{class_type}.{input_name} 模型未出现在 ComfyUI 列表：{current}")
    return errors


def workflow_risk_checks(*, mode: str, hardware: dict[str, Any], width: int, height: int, length: int) -> list[dict[str, str]]:
    checks: list[dict[str, str]] = []
    strategy = hardware.get("platform_strategy")
    if strategy != "mac_mps":
        return checks

    mps_ready = bool(hardware.get("front_torch_mps_ready") or hardware.get("comfy_torch_mps_ready"))
    if mode in {"i2v_a14b", "t2v_a14b"}:
        checks.append(
            {
                "level": "blocked",
                "title": "A14B Mac 专家风险",
                "message": "Wan2.2 A14B 的 fp8/MPS 兼容风险较高，Mac 默认不开放。请先使用 LTX 或 Wan5B 实验档。",
            }
        )
    if mode == "ti2v_5b":
        checks.append(
            {
                "level": "warn",
                "title": "Wan5B MPS smoke test",
                "message": "Mac 上运行 Wan5B 前建议先用 512x320、9 到 17 帧、低步数跑一次 smoke test，再提高到当前分辨率。",
            }
        )
        if width >= 1280 or height >= 704 or length > 49:
            checks.append(
                {
                    "level": "warn",
                    "title": "720P 高内存实验",
                    "message": "当前参数属于 Mac 高内存实验范围；如果出现 OOM、dtype 或黑屏，先退回 480P/短帧数。",
                }
            )
    if mode in {"ltx_i2v", "ltx_t2v"} and not mps_ready:
        checks.append(
            {
                "level": "warn",
                "title": "MPS 状态未确认",
                "message": "未在前端或 ComfyUI Python 中确认 torch.backends.mps 可用；这不一定代表不能跑，但建议先启动 ComfyUI 后刷新环境。",
            }
        )
    return checks


async def apply_comfy_model_option_names(graph: dict[str, Any]) -> dict[str, Any]:
    try:
        object_info = await comfy_get("/object_info")
    except HTTPException:
        return graph
    return resolve_graph_model_option_names(graph, object_info)


def collect_model_registry_checks(object_info: dict[str, Any] | None) -> list[dict[str, Any]]:
    expectations = [
        {
            "id": "ti2v_5b",
            "label": "Wan2.2 TI2V-5B fp16",
            "node": "UNETLoader",
            "input": "unet_name",
            "name": "Wan2.2/wan2.2_ti2v_5B_fp16.safetensors",
        },
        {
            "id": "i2v_high",
            "label": "Wan2.2 I2V-A14B high noise fp8",
            "node": "UNETLoader",
            "input": "unet_name",
            "name": "Wan2.2/wan2.2_i2v_high_noise_14B_fp8_scaled.safetensors",
        },
        {
            "id": "i2v_low",
            "label": "Wan2.2 I2V-A14B low noise fp8",
            "node": "UNETLoader",
            "input": "unet_name",
            "name": "Wan2.2/wan2.2_i2v_low_noise_14B_fp8_scaled.safetensors",
        },
        {
            "id": "t2v_high",
            "label": "Wan2.2 T2V-A14B high noise fp8",
            "node": "UNETLoader",
            "input": "unet_name",
            "name": "Wan2.2/wan2.2_t2v_high_noise_14B_fp8_scaled.safetensors",
        },
        {
            "id": "t2v_low",
            "label": "Wan2.2 T2V-A14B low noise fp8",
            "node": "UNETLoader",
            "input": "unet_name",
            "name": "Wan2.2/wan2.2_t2v_low_noise_14B_fp8_scaled.safetensors",
        },
        {
            "id": "umt5",
            "label": "UMT5 XXL fp8 文本编码器",
            "node": "CLIPLoader",
            "input": "clip_name",
            "name": "umt5_xxl_fp8_e4m3fn_scaled.safetensors",
        },
        {
            "id": "wan21_vae",
            "label": "Wan 2.1 VAE",
            "node": "VAELoader",
            "input": "vae_name",
            "name": "wan_2.1_vae.safetensors",
        },
        {
            "id": "wan22_vae",
            "label": "Wan 2.2 VAE",
            "node": "VAELoader",
            "input": "vae_name",
            "name": "wan2.2_vae.safetensors",
        },
        {
            "id": "ltx_2b_095",
            "label": "LTX-Video 2B 0.9.5",
            "node": "CheckpointLoaderSimple",
            "input": "ckpt_name",
            "name": "ltx-video-2b-v0.9.5.safetensors",
        },
        {
            "id": "t5xxl_fp16",
            "label": "T5 XXL fp16 文本编码器",
            "node": "CLIPLoader",
            "input": "clip_name",
            "name": "t5xxl_fp16.safetensors",
        },
        {
            "id": "i2v_lora_high",
            "label": "I2V 4步 LoRA high noise",
            "node": "LoraLoaderModelOnly",
            "input": "lora_name",
            "name": "Wan2.2/wan2.2_i2v_lightx2v_4steps_lora_v1_high_noise.safetensors",
        },
        {
            "id": "i2v_lora_low",
            "label": "I2V 4步 LoRA low noise",
            "node": "LoraLoaderModelOnly",
            "input": "lora_name",
            "name": "Wan2.2/wan2.2_i2v_lightx2v_4steps_lora_v1_low_noise.safetensors",
        },
        {
            "id": "t2v_lora_high",
            "label": "T2V 4步 LoRA high noise",
            "node": "LoraLoaderModelOnly",
            "input": "lora_name",
            "name": "Wan2.2/wan2.2_t2v_lightx2v_4steps_lora_v1.1_high_noise.safetensors",
        },
        {
            "id": "t2v_lora_low",
            "label": "T2V 4步 LoRA low noise",
            "node": "LoraLoaderModelOnly",
            "input": "lora_name",
            "name": "Wan2.2/wan2.2_t2v_lightx2v_4steps_lora_v1.1_low_noise.safetensors",
        },
        {
            "id": "realesrgan_x2",
            "label": "RealESRGAN x2 视频超分权重",
            "node": "UpscaleModelLoader",
            "input": "model_name",
            "name": "RealESRGAN_x2plus.pth",
        },
        {
            "id": "ultrasharp_x4",
            "label": "4x-UltraSharp 备用超分权重",
            "node": "UpscaleModelLoader",
            "input": "model_name",
            "name": "4x-UltraSharp.pth",
        },
    ]
    checks: list[dict[str, Any]] = []
    for item in expectations:
        options = node_input_options(object_info, item["node"], item["input"])
        ok = model_option_matches(options, item["name"])
        reason = "已出现在 ComfyUI 模型列表" if ok else "ComfyUI 模型列表未加载"
        if object_info is None:
            reason = "ComfyUI 未连接"
        elif item["node"] not in object_info:
            reason = f"节点未加载：{item['node']}"
        checks.append(
            {
                **item,
                "ok": ok,
                "reason": reason,
                "options_count": len(options),
            }
        )
    return checks


def build_load_diagnostics(
    *,
    paths: dict[str, Any],
    assets: list[dict[str, Any]],
    custom_nodes: list[dict[str, Any]],
    node_checks: list[dict[str, Any]],
    registry_checks: list[dict[str, Any]],
    object_info: dict[str, Any] | None,
) -> list[dict[str, str]]:
    diagnostics: list[dict[str, str]] = []
    if paths.get("base_dir_mismatch"):
        diagnostics.append(
            {
                "level": "warn",
                "title": "检测到 ComfyUI 实际目录和前端默认目录不同",
                "message": f"本次会按 ComfyUI 正在使用的目录工作：{paths['base_dir']}。如果之前下载到 {BASE_DIR}，那些文件不会被当前 ComfyUI 自动加载。",
            }
        )

    asset_by_id = {item["id"]: item for item in assets}
    loaded_missing = [
        item
        for item in registry_checks
        if not item["ok"] and asset_by_id.get(item["id"], {}).get("ok")
    ]
    if loaded_missing and object_info is not None:
        labels = "、".join(item["label"] for item in loaded_missing[:4])
        suffix = " 等" if len(loaded_missing) > 4 else ""
        diagnostics.append(
            {
                "level": "blocked",
                "title": "文件已存在，但 ComfyUI 模型列表没有加载",
                "message": f"{labels}{suffix} 已在磁盘上，但没有出现在 ComfyUI 的模型下拉列表里。通常是下载目录不一致，或安装后还没有重启 ComfyUI。",
            }
        )

    node_by_name = {item["name"]: item for item in node_checks}
    custom_by_id = {item["id"]: item for item in custom_nodes}
    if custom_by_id.get("video_helper_suite", {}).get("ok") and not (
        node_by_name.get("VHS_LoadVideo", {}).get("ok") and node_by_name.get("VHS_VideoCombine", {}).get("ok")
    ):
        diagnostics.append(
            {
                "level": "blocked",
                "title": "VideoHelperSuite 目录存在，但节点未加载",
                "message": "这通常是 ComfyUI 没有重启，或自定义节点 requirements 没装进 ComfyUI 的 Python 环境。请重新执行一键安装缺失项，然后重启 ComfyUI。",
            }
        )
    if custom_by_id.get("frame_interpolation", {}).get("ok") and not node_by_name.get("RIFE VFI", {}).get("ok"):
        diagnostics.append(
            {
                "level": "blocked",
                "title": "Frame-Interpolation 目录存在，但 RIFE 节点未加载",
                "message": "安装脚本现在会自动尝试安装该节点的 requirements。安装后需要重启 ComfyUI 才会出现 RIFE VFI 节点。",
            }
        )

    partial = [item for item in assets if item.get("exists") and not item.get("ok")]
    if partial:
        labels = "、".join(item["label"] for item in partial[:3])
        diagnostics.append(
            {
                "level": "warn",
                "title": "发现未完整下载的文件",
                "message": f"{labels} 文件大小不匹配。一键安装会尝试断点续传，若仍失败请删除对应 partial 文件后重试。",
            }
        )

    if not diagnostics:
        diagnostics.append(
            {
                "level": "ok",
                "title": "加载链路正常",
                "message": "模型文件、自定义节点目录和 ComfyUI 加载状态没有发现明显不一致。",
            }
        )
    return diagnostics


def model_recommendations(
    *,
    os_info: dict[str, Any],
    hardware: dict[str, Any],
    assets: list[dict[str, Any]],
    custom_nodes: list[dict[str, Any]],
    tool_checks: list[dict[str, Any]],
    node_checks: list[dict[str, Any]],
    base_dir: Path | None = None,
) -> list[dict[str, Any]]:
    base_dir = (base_dir or BASE_DIR).expanduser().resolve()
    asset_ok = {item["id"]: bool(item["ok"]) for item in assets}
    node_ok = {item["name"]: bool(item["ok"]) for item in node_checks}
    custom_ok = {item["id"]: bool(item["ok"]) for item in custom_nodes}
    tool_ok = {item["name"]: bool(item["ok"]) for item in tool_checks}
    accelerator = hardware.get("accelerator")
    max_vram = float(hardware.get("max_vram_gb") or 0)
    sum_vram = float(hardware.get("sum_vram_gb") or 0)
    gpu_count = int(hardware.get("gpu_count") or 0)
    is_macos = os_info["name"] == "Darwin"
    platform_strategy = hardware.get("platform_strategy")
    mac_tier = str(hardware.get("mac_video_tier") or "")
    mac_info = hardware.get("mac") or {}
    mac_memory = float(mac_info.get("unified_memory_gb") or hardware.get("system_memory_gb") or 0)
    mac_mps_ready = bool(hardware.get("front_torch_mps_ready") or hardware.get("comfy_torch_mps_ready"))

    def all_assets(*ids: str) -> bool:
        return all(asset_ok.get(item) for item in ids)

    def all_nodes(*names: str) -> bool:
        return all(node_ok.get(item) for item in names)

    recommendations: list[dict[str, Any]] = []

    has_flux = (base_dir / "models" / "diffusion_models" / "flux2_dev_fp8mixed.safetensors").exists()
    has_sdxl = any((base_dir / "models" / "checkpoints").glob("*.safetensors"))
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
    ltx_ready = all_assets("ltx_2b_095", "t5xxl_fp16") and all_nodes(
        "CheckpointLoaderSimple",
        "CLIPLoader",
        "CLIPTextEncode",
        "LTXVConditioning",
        "LTXVScheduler",
        "LTXVImgToVideo",
        "SamplerCustom",
        "VAEDecode",
        "CreateVideo",
        "SaveVideo",
    )
    if platform_strategy == "mac_mps":
        if ltx_ready and mac_mps_ready:
            status_text = "ok"
            reason = f"检测到 Apple Silicon 与约 {mac_memory:g}GB 统一内存，默认用 LTX 短镜头跑通 Mac 视频生成。"
        elif ltx_ready:
            status_text = "warn"
            reason = "LTX 模型和节点已在，但当前没有确认 ComfyUI 的 MPS torch 状态；可先跑低分辨率 smoke test。"
        else:
            status_text = "warn"
            reason = "Mac 默认走 LTX 小模型路线；一键配置会先补齐 LTX 2B 与 T5 文本编码器。"
        recommended_ltx = ["Mac LTX I2V 低档/均衡档", "512x320 或 576x320", "2 到 3 秒短镜头"]
        if mac_tier in {"mac_ltx_quality", "mac_wan5b_480p", "mac_wan5b_720p_experimental"}:
            recommended_ltx = ["Mac LTX I2V 质量档", "704x416 或 768x512", "2 到 4 秒短镜头"]
        recommendations.append(
            {
                "step": "试镜头",
                "status": status_text,
                "recommended": recommended_ltx,
                "reason": reason,
            }
        )
    elif not ti2v_ready:
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
    if platform_strategy != "mac_mps":
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
    if platform_strategy == "mac_mps":
        if mac_tier == "mac_wan5b_720p_experimental":
            formal_status = "warn" if ti2v_ready else "warn"
            formal_recommended = ["Wan2.2 TI2V-5B 720P 高内存实验档", "优先关键帧 I2V/TI2V", "先跑 smoke test"]
            formal_reason = "96GB/128GB+ Apple Silicon 可以尝试 Wan5B 720P 短镜头，但仍需通过 MPS dtype 与节点预检；A14B 不默认开放。"
        elif mac_tier == "mac_wan5b_480p":
            formal_status = "warn"
            formal_recommended = ["Wan2.2 TI2V-5B 480P 实验档", "短镜头", "必要时降低帧数"]
            formal_reason = "48GB/64GB Apple Silicon 可尝试 Wan5B 480P；720P 仍标为实验。"
        else:
            formal_status = "ok" if ltx_ready and mac_mps_ready else "warn"
            if mac_tier == "mac_ltx_low":
                formal_recommended = ["Mac LTX I2V 低档", "512x320", "1 到 2 秒短镜头"]
                formal_reason = "该 Mac 档位优先保证跑通，正式画质通过更短分镜、去闪烁和后期超分补足。"
            else:
                formal_recommended = ["Mac LTX I2V 质量档", "短镜头分镜", "后期超分"]
                formal_reason = "该 Mac 档位更适合 LTX 小模型短片段，正式画质通过分镜、去闪烁和超分补足。"
        recommendations.append(
            {
                "step": "正式片段",
                "status": formal_status,
                "recommended": formal_recommended,
                "reason": formal_reason,
            }
        )
    elif not i2v_ready:
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
    if platform_strategy != "mac_mps":
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
    if platform_strategy == "mac_mps":
        recommendations.append(
            {
                "step": "无图生视频",
                "status": "ok" if ltx_ready and mac_mps_ready else "warn",
                "recommended": ["Mac LTX T2V 低档/均衡档"],
                "reason": "Mac 上也可以文字生视频，但仍建议优先 I2V，人物和空间更可控。",
            }
        )
    else:
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
    base_dir: Path | None = None,
) -> dict[str, Any]:
    base_dir = (base_dir or BASE_DIR).expanduser().resolve()
    asset_ok = {item["id"]: bool(item["ok"]) for item in assets}
    node_ok = {item["name"]: bool(item["ok"]) for item in node_checks}
    custom_ok = {item["id"]: bool(item["ok"]) for item in custom_nodes}
    tool_ok = {item["name"]: bool(item["ok"]) for item in tool_checks}
    accelerator = hardware.get("accelerator")
    max_vram = float(hardware.get("max_vram_gb") or 0)
    is_macos = os_info["name"] == "Darwin"
    platform_strategy = hardware.get("platform_strategy")
    mac_tier = str(hardware.get("mac_video_tier") or "")
    mac_info = hardware.get("mac") or {}
    mac_memory = float(mac_info.get("unified_memory_gb") or hardware.get("system_memory_gb") or 0)
    mac_mps_ready = bool(hardware.get("front_torch_mps_ready") or hardware.get("comfy_torch_mps_ready"))

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

    def mac_ltx_status(ready: bool) -> str:
        if platform_strategy != "mac_mps":
            return "blocked"
        if ready and mac_mps_ready:
            return "ok"
        return "warn"

    def mac_wan5b_status(ready: bool, *, min_memory_gb: float) -> str:
        if platform_strategy != "mac_mps" or mac_memory < min_memory_gb:
            return "blocked"
        if ready and mac_mps_ready:
            return "warn"
        return "warn"

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
    ltx_ready = all_assets("ltx_2b_095", "t5xxl_fp16") and all_nodes(
        "CheckpointLoaderSimple",
        "CLIPLoader",
        "CLIPTextEncode",
        "LTXVConditioning",
        "LTXVScheduler",
        "LTXVImgToVideo",
        "SamplerCustom",
        "VAEDecode",
        "CreateVideo",
        "SaveVideo",
    )
    deflicker_ready = bool(tool_ok.get("ffmpeg") and tool_ok.get("ffmpeg:deflicker") and tool_ok.get("ffmpeg:hqdn3d"))
    rife_ready = bool(asset_ok.get("rife49") and custom_ok.get("frame_interpolation") and all_nodes("RIFE VFI"))
    upscale_ready = all_nodes("UpscaleModelLoader", "ImageUpscaleWithModel")
    has_flux = (base_dir / "models" / "diffusion_models" / "flux2_dev_fp8mixed.safetensors").exists()
    has_sdxl = any((base_dir / "models" / "checkpoints").glob("*.safetensors"))

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
                id="mac_ltx_low_i2v",
                label="Mac LTX I2V 低档",
                step="draft",
                mode="ltx_i2v",
                status=mac_ltx_status(ltx_ready),
                reason="Apple Silicon 低内存或首次 smoke test 档；小分辨率、短镜头，优先验证能跑通。",
                defaults={"width": 512, "height": 320, "length": 25, "fps": 24, "steps": 12, "cfg": 3.0},
            ),
            option(
                id="mac_ltx_balanced_i2v",
                label="Mac LTX I2V 均衡档",
                step="draft",
                mode="ltx_i2v",
                status=mac_ltx_status(ltx_ready),
                reason="16GB 到 32GB+ Apple Silicon 的默认 Mac 视频草稿路线，速度和稳定性优先。",
                defaults={"width": 576, "height": 320, "length": 49, "fps": 24, "steps": 16, "cfg": 3.0},
            ),
            option(
                id="mac_ltx_quality_i2v",
                label="Mac LTX I2V 质量档",
                step="draft",
                mode="ltx_i2v",
                status=mac_ltx_status(ltx_ready),
                reason="高内存 Apple Silicon 可先用 LTX 质量档试动作和镜头，再决定是否切 Wan5B。",
                defaults={"width": 704, "height": 416, "length": 49, "fps": 24, "steps": 18, "cfg": 3.0},
            ),
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
                id="mac_ltx_low_i2v",
                label="Mac LTX I2V 低档",
                step="final",
                mode="ltx_i2v",
                status=mac_ltx_status(ltx_ready),
                reason="8GB/16GB Apple Silicon 的保守正式片段档。短镜头、小分辨率，优先保证跑通。",
                defaults={"width": 512, "height": 320, "length": 25, "fps": 24, "steps": 12, "cfg": 3.0},
            ),
            option(
                id="mac_ltx_quality_i2v",
                label="Mac LTX I2V 质量档",
                step="final",
                mode="ltx_i2v",
                status=mac_ltx_status(ltx_ready),
                reason="Mac 默认正式片段路线。先用关键帧稳定构图，再靠短镜头、去闪烁和超分补质量。",
                defaults={"width": 704, "height": 416, "length": 49, "fps": 24, "steps": 18, "cfg": 3.0},
            ),
            option(
                id="mac_ltx_balanced_t2v",
                label="Mac LTX T2V 均衡档",
                step="final",
                mode="ltx_t2v",
                status=mac_ltx_status(ltx_ready),
                reason="没有关键帧时可用；随机性更强，仍建议先做关键帧再 I2V。",
                defaults={"width": 576, "height": 320, "length": 49, "fps": 24, "steps": 16, "cfg": 3.0},
                uses_image=False,
            ),
            option(
                id="mac_wan5b_480p",
                label="Mac Wan2.2 TI2V-5B 480P 实验档",
                step="final",
                mode="ti2v_5b",
                status=mac_wan5b_status(ti2v_ready, min_memory_gb=24),
                reason="高内存 Apple Silicon 的质量尝试档。需要先通过节点、模型名和 MPS dtype 预检。",
                defaults={"width": 832, "height": 480, "length": 49, "fps": 24, "steps": 20, "cfg": 5.0},
            ),
            option(
                id="mac_wan5b_720p_experimental",
                label="Mac Wan2.2 TI2V-5B 720P 高内存实验档",
                step="final",
                mode="ti2v_5b",
                status=mac_wan5b_status(ti2v_ready, min_memory_gb=96),
                reason="96GB/128GB+ Apple Silicon 可尝试的短镜头档；A14B 仍不作为 Mac 默认推荐。",
                defaults={"width": 1280, "height": 704, "length": 49, "fps": 24, "steps": 20, "cfg": 5.0},
            ),
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

    if platform_strategy == "mac_mps":
        if mac_tier == "mac_wan5b_720p_experimental":
            final_default = "mac_wan5b_720p_experimental"
            draft_default = "mac_ltx_quality_i2v"
        elif mac_tier == "mac_wan5b_480p":
            final_default = "mac_wan5b_480p"
            draft_default = "mac_ltx_quality_i2v"
        elif mac_tier == "mac_ltx_quality":
            final_default = "mac_ltx_quality_i2v"
            draft_default = "mac_ltx_balanced_i2v"
        elif mac_tier == "mac_ltx_balanced":
            final_default = "mac_ltx_quality_i2v"
            draft_default = "mac_ltx_balanced_i2v"
        else:
            final_default = "mac_ltx_low_i2v"
            draft_default = "mac_ltx_low_i2v"
    elif accelerator == "cuda" and max_vram >= 80:
        final_default = "wan22_i2v_a14b_720p"
        draft_default = "wan22_ti2v_5b_720p"
    elif accelerator == "cuda" and max_vram >= 48:
        final_default = "wan22_i2v_a14b_480p"
        draft_default = "wan22_ti2v_5b_720p"
    elif accelerator == "cuda" and max_vram >= 16:
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
            "route": "8GB/16GB 默认 Mac LTX 低档；24GB-36GB 用 LTX 均衡/质量档；48GB+ 可试 Wan2.2 TI2V-5B 480P；96GB/128GB+ 可试 720P 实验档。",
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


INSTALL_PROFILE_ASSETS = {
    "cuda-full": {
        "ti2v_5b",
        "i2v_high",
        "i2v_low",
        "t2v_high",
        "t2v_low",
        "umt5",
        "wan21_vae",
        "wan22_vae",
        "i2v_lora_high",
        "i2v_lora_low",
        "t2v_lora_high",
        "t2v_lora_low",
        "rife49",
        "realesrgan_x2",
        "ultrasharp_x4",
        "sample_keyframe",
    },
    "mac-low": {"ltx_2b_095", "t5xxl_fp16", "sample_keyframe"},
    "mac-balanced": {"ltx_2b_095", "t5xxl_fp16", "sample_keyframe"},
    "mac-wan5b": {"ltx_2b_095", "t5xxl_fp16", "ti2v_5b", "umt5", "wan22_vae", "sample_keyframe"},
    "post-only": {"rife49", "realesrgan_x2", "ultrasharp_x4", "sample_keyframe"},
}

INSTALL_PROFILE_CUSTOM_NODES = {
    "cuda-full": {"video_helper_suite", "frame_interpolation"},
    "mac-low": set(),
    "mac-balanced": set(),
    "mac-wan5b": set(),
    "post-only": {"video_helper_suite", "frame_interpolation"},
}


def normalize_install_profile(profile: str | None) -> str:
    profile = (profile or "auto").strip()
    if profile in {"cuda-full", "mac-low", "mac-balanced", "mac-wan5b", "post-only"}:
        return profile
    return "auto"


def install_profile_for_hardware(hardware: dict[str, Any]) -> str:
    strategy = hardware.get("platform_strategy")
    tier = str(hardware.get("mac_video_tier") or "")
    if strategy == "mac_mps":
        if tier.startswith("mac_wan5b"):
            return "mac-wan5b"
        if tier in {"mac_ltx_balanced", "mac_ltx_quality"}:
            return "mac-balanced"
        return "mac-low"
    if strategy == "mac_post_only" or strategy == "post_only":
        return "post-only"
    return "cuda-full"


def filter_assets_for_install_profile(assets: list[dict[str, Any]], profile: str) -> list[dict[str, Any]]:
    allowed = INSTALL_PROFILE_ASSETS.get(profile, INSTALL_PROFILE_ASSETS["cuda-full"])
    return [item for item in assets if item.get("id") in allowed]


def filter_custom_nodes_for_install_profile(custom_nodes: list[dict[str, Any]], profile: str) -> list[dict[str, Any]]:
    allowed = INSTALL_PROFILE_CUSTOM_NODES.get(profile, INSTALL_PROFILE_CUSTOM_NODES["cuda-full"])
    return [item for item in custom_nodes if item.get("id") in allowed]


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


def comfy_venv_python() -> Path:
    if platform.system() == "Windows":
        return COMFY_INSTALL_DIR / ".venv" / "Scripts" / "python.exe"
    return COMFY_INSTALL_DIR / ".venv" / "bin" / "python"


def comfy_port() -> int:
    parsed = urlparse(COMFY_URL)
    if parsed.port:
        return int(parsed.port)
    return 8000


def comfy_listen_host() -> str:
    parsed = urlparse(COMFY_URL)
    return parsed.hostname or "127.0.0.1"


def bootstrap_status_payload(
    comfy_connected: bool,
    comfy_error: str = "",
    paths: dict[str, Any] | None = None,
) -> dict[str, Any]:
    main_py = COMFY_INSTALL_DIR / "main.py"
    python = comfy_venv_python()
    paths = paths or configured_comfy_paths()
    return {
        "base_dir": str(BASE_DIR),
        "active_base_dir": str(paths["base_dir"]),
        "paths": serializable_comfy_paths(paths),
        "install_dir": str(COMFY_INSTALL_DIR),
        "comfy_url": COMFY_URL,
        "comfy_connected": comfy_connected,
        "comfy_error": comfy_error,
        "comfy_repo_exists": main_py.exists(),
        "comfy_main": str(main_py),
        "venv_python": str(python),
        "venv_ready": python.exists(),
        "git_ready": bool(shutil.which("git")),
        "python": sys.version.split()[0],
        "platform": platform.system(),
        "can_install_comfyui": bool(shutil.which("git")),
        "can_start_comfyui": main_py.exists() and python.exists(),
        "running_from_launcher": True,
    }


def run_comfyui_worker(job_id: str, command: list[str]) -> None:
    global COMFY_PROCESS
    job = INSTALL_JOBS[job_id]
    job["status"] = "running"
    job["started_at"] = now_ms()
    job["log"].append(f"启动 ComfyUI：{install_command_text(command)}")
    creationflags = 0
    if platform.system() == "Windows":
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        COMFY_PROCESS = subprocess.Popen(
            command,
            cwd=str(COMFY_INSTALL_DIR),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=creationflags,
        )
        assert COMFY_PROCESS.stdout is not None
        for line in COMFY_PROCESS.stdout:
            job["log"].append(line.rstrip())
            if len(job["log"]) > 600:
                job["log"] = job["log"][-600:]
        return_code = COMFY_PROCESS.wait()
        job["return_code"] = return_code
        job["status"] = "stopped" if return_code == 0 else "failed"
        job["completed"] = True
        job["finished_at"] = now_ms()
        job["log"].append(f"ComfyUI 已退出，退出码：{return_code}")
    except Exception as exc:
        job["status"] = "failed"
        job["completed"] = True
        job["finished_at"] = now_ms()
        job["error"] = str(exc)
        job["log"].append(f"ComfyUI 启动失败：{exc}")


def run_deflicker(source: Path, paths: dict[str, Any] | None = None) -> dict[str, Any]:
    if source.suffix.lower() not in VIDEO_EXTENSIONS:
        raise HTTPException(status_code=400, detail="闪烁修复只支持视频文件")

    paths = paths or configured_comfy_paths()
    output_dir = Path(paths["output_dir"]) / "wan22_frontend"
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

    return output_media_item(output, paths)


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/favicon.ico", include_in_schema=False)
async def favicon() -> Response:
    return Response(status_code=204)


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

    comfy_paths = comfy_paths_from_system_stats(system_stats)
    active_base_dir = Path(comfy_paths["base_dir"])
    assets = collect_asset_checks(active_base_dir)
    custom_nodes = collect_custom_node_checks(active_base_dir)
    node_checks = collect_node_checks(object_info)
    registry_checks = collect_model_registry_checks(object_info)
    diagnostics = build_load_diagnostics(
        paths=comfy_paths,
        assets=assets,
        custom_nodes=custom_nodes,
        node_checks=node_checks,
        registry_checks=registry_checks,
        object_info=object_info,
    )

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
    install_profile = install_profile_for_hardware(hardware)
    mac_info = hardware.get("mac") or {}
    if mac_info.get("is_macos") and mac_info.get("apple_silicon"):
        if not (hardware.get("torch") or {}).get("available"):
            diagnostics.insert(
                0,
                {
                    "level": "warn",
                    "title": "前端 Python 未安装 torch",
                    "message": "这不代表 ComfyUI 不能使用 MPS。系统会单独检测 ComfyUI venv 的 torch.backends.mps 状态。",
                },
            )
        if not (hardware.get("comfy_torch") or {}).get("exists"):
            diagnostics.insert(
                0,
                {
                    "level": "warn",
                    "title": "ComfyUI Python 尚未确认",
                    "message": "未找到前端配置的 ComfyUI venv；若使用 ComfyUI Desktop，请先启动后刷新，系统会从 /system_stats 和 /object_info 继续预检。",
                },
            )
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
        base_dir=active_base_dir,
    )
    model_options = workflow_model_options(
        os_info=os_info,
        hardware=hardware,
        assets=assets,
        custom_nodes=custom_nodes,
        tool_checks=tool_checks,
        node_checks=node_checks,
        base_dir=active_base_dir,
    )
    install_assets = filter_assets_for_install_profile(assets, install_profile)
    install_custom_nodes = filter_custom_nodes_for_install_profile(custom_nodes, install_profile)
    missing_installable = installable_missing_items(install_assets, install_custom_nodes)
    repair_needed = any(item.get("level") == "blocked" for item in diagnostics)
    if repair_needed and not missing_installable:
        missing_installable.append(
            {
                "id": "repair_loaded_assets",
                "label": "修复已下载但未加载的节点/模型",
                "path": str(active_base_dir),
                "reason": "重新校验文件、安装自定义节点 requirements，并提示重启 ComfyUI。",
            }
        )
    core_steps = {"试镜头", "正式片段", "闪烁修复", "插帧", "清晰度增强"}
    blocked = [
        item
        for item in recommendations
        if item["step"] in core_steps and item["status"] == "blocked"
    ]

    return {
        "ok": bool(comfy_info["connected"] and not blocked),
        "base_dir": str(active_base_dir),
        "configured_base_dir": str(BASE_DIR),
        "workspace_dir": str(WORKSPACE_DIR),
        "paths": serializable_comfy_paths(comfy_paths),
        "os": os_info,
        "comfy": comfy_info,
        "hardware": hardware,
        "install_profile": install_profile,
        "assets": assets,
        "custom_nodes": custom_nodes,
        "nodes": node_checks,
        "model_registry": registry_checks,
        "diagnostics": diagnostics,
        "tools": tool_checks,
        "recommendations": recommendations,
        "model_options": model_options,
        "missing_installable": missing_installable,
        "needs_install": bool(missing_installable),
        "repair_needed": repair_needed,
        "blocked": blocked,
        "install_note": f"一键安装将按 {install_profile} 档位下载缺失文件；安装后需要重启 ComfyUI，让模型列表重新加载。",
    }


@app.get("/api/bootstrap")
async def bootstrap_status() -> dict[str, Any]:
    try:
        system_stats = await comfy_get("/system_stats")
        return bootstrap_status_payload(True, paths=comfy_paths_from_system_stats(system_stats))
    except HTTPException as exc:
        return bootstrap_status_payload(False, str(exc.detail))


@app.post("/api/bootstrap/install-comfyui")
async def install_comfyui(backend: str = "auto") -> dict[str, Any]:
    if backend not in {"auto", "cuda", "cpu", "mps", "skip"}:
        raise HTTPException(status_code=400, detail="未知 PyTorch 后端")
    job_id = uuid.uuid4().hex
    job = {
        "id": job_id,
        "kind": "install_comfyui",
        "status": "queued",
        "completed": False,
        "created_at": now_ms(),
        "log": [],
    }
    INSTALL_JOBS[job_id] = job
    script = WORKSPACE_DIR / "scripts" / "install_comfyui.py"
    if not script.exists():
        job["status"] = "failed"
        job["completed"] = True
        job["finished_at"] = now_ms()
        job["log"].append(f"安装脚本不存在：{script}")
        return job
    command = [
        sys.executable,
        "-u",
        str(script),
        "--base-dir",
        str(BASE_DIR),
        "--install-dir",
        str(COMFY_INSTALL_DIR),
        "--backend",
        backend,
    ]
    job["command"] = install_command_text(command)
    thread = threading.Thread(target=run_install_worker, args=(job_id, command), daemon=True)
    thread.start()
    return job


@app.post("/api/bootstrap/start-comfyui")
async def start_comfyui() -> dict[str, Any]:
    global COMFY_PROCESS
    try:
        await comfy_get("/system_stats")
        job_id = uuid.uuid4().hex
        job = {
            "id": job_id,
            "kind": "start_comfyui",
            "status": "success",
            "completed": True,
            "created_at": now_ms(),
            "finished_at": now_ms(),
            "log": [f"ComfyUI 已在运行：{COMFY_URL}"],
        }
        INSTALL_JOBS[job_id] = job
        return job
    except HTTPException:
        pass

    main_py = COMFY_INSTALL_DIR / "main.py"
    python = comfy_venv_python()
    if not main_py.exists():
        raise HTTPException(status_code=400, detail=f"ComfyUI 未安装：{main_py}")
    if not python.exists():
        raise HTTPException(status_code=400, detail=f"ComfyUI venv 未就绪：{python}")
    if COMFY_PROCESS and COMFY_PROCESS.poll() is None:
        job_id = uuid.uuid4().hex
        job = {
            "id": job_id,
            "kind": "start_comfyui",
            "status": "running",
            "completed": False,
            "created_at": now_ms(),
            "log": ["ComfyUI 启动进程已经在运行。"],
        }
        INSTALL_JOBS[job_id] = job
        return job

    command = [
        str(python),
        str(main_py),
        "--listen",
        comfy_listen_host(),
        "--port",
        str(comfy_port()),
        "--base-directory",
        str(BASE_DIR),
        "--input-directory",
        str(INPUT_DIR),
        "--output-directory",
        str(OUTPUT_DIR),
        "--user-directory",
        str(BASE_DIR / "user"),
    ]
    job_id = uuid.uuid4().hex
    job = {
        "id": job_id,
        "kind": "start_comfyui",
        "status": "queued",
        "completed": False,
        "created_at": now_ms(),
        "log": [],
        "command": install_command_text(command),
    }
    INSTALL_JOBS[job_id] = job
    thread = threading.Thread(target=run_comfyui_worker, args=(job_id, command), daemon=True)
    thread.start()
    return job


@app.post("/api/install")
async def install_workflow_assets(profile: str = "auto") -> dict[str, Any]:
    comfy_paths = await active_comfy_paths()
    active_base_dir = Path(comfy_paths["base_dir"])
    assets = collect_asset_checks(active_base_dir)
    custom_nodes = collect_custom_node_checks(active_base_dir)
    normalized_profile = normalize_install_profile(profile)
    try:
        system_stats_for_profile = await comfy_get("/system_stats")
    except HTTPException:
        system_stats_for_profile = None
    hardware = build_hardware_summary(system_stats_for_profile)
    if normalized_profile == "auto":
        normalized_profile = install_profile_for_hardware(hardware)
    install_assets = filter_assets_for_install_profile(assets, normalized_profile)
    install_custom_nodes = filter_custom_nodes_for_install_profile(custom_nodes, normalized_profile)
    missing = installable_missing_items(install_assets, install_custom_nodes)
    object_info: dict[str, Any] | None = None
    try:
        object_info = await comfy_get("/object_info")
    except HTTPException:
        object_info = None
    diagnostics = build_load_diagnostics(
        paths=comfy_paths,
        assets=assets,
        custom_nodes=custom_nodes,
        node_checks=collect_node_checks(object_info),
        registry_checks=collect_model_registry_checks(object_info),
        object_info=object_info,
    )
    repair_needed = any(item.get("level") == "blocked" for item in diagnostics)
    job_id = uuid.uuid4().hex
    job = {
        "id": job_id,
        "status": "queued",
        "completed": False,
        "created_at": now_ms(),
        "base_dir": str(active_base_dir),
        "paths": serializable_comfy_paths(comfy_paths),
        "install_profile": normalized_profile,
        "missing": missing,
        "repair_needed": repair_needed,
        "log": [],
    }
    INSTALL_JOBS[job_id] = job

    if not missing and not repair_needed:
        job["status"] = "success"
        job["completed"] = True
        job["finished_at"] = now_ms()
        job["skipped"] = True
        job["log"].append(f"安装目标目录：{active_base_dir}")
        job["log"].append("所有可一键安装的模型、权重和自定义节点都已存在，无需下载。")
        return job

    script = WORKSPACE_DIR / "scripts" / "install_workflow_assets.py"
    if not script.exists():
        job["status"] = "failed"
        job["completed"] = True
        job["finished_at"] = now_ms()
        job["log"].append(f"安装脚本不存在：{script}")
        return job

    command = [
        sys.executable,
        "-u",
        str(script),
        "--base-dir",
        str(active_base_dir),
        "--profile",
        normalized_profile,
    ]
    python = comfy_venv_python()
    if python.exists():
        command.extend(["--comfy-python", str(python)])
    job["command"] = install_command_text(command)
    if comfy_paths.get("base_dir_mismatch"):
        job["log"].append(f"检测到 ComfyUI 正在使用 {active_base_dir}，本次安装会写入该目录。")
        job["log"].append(f"前端默认目录是 {BASE_DIR}，如之前下载到默认目录，当前 ComfyUI 不会自动扫描。")
    if repair_needed and not missing:
        job["log"].append("未发现缺失文件，但发现已下载未加载的问题；将重新安装自定义节点依赖并校验文件。")
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
    comfy_paths = await active_comfy_paths()
    active_base_dir = Path(comfy_paths["base_dir"])
    input_dir = Path(comfy_paths["input_dir"])
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
        "CheckpointLoaderSimple",
        "EmptyLTXVLatentVideo",
        "LTXVConditioning",
        "LTXVScheduler",
        "LTXVImgToVideo",
        "LTXVPreprocess",
        "SamplerCustom",
        "KSamplerSelect",
        "VAEDecode",
        "VHS_LoadVideo",
        "VHS_VideoCombine",
        "RIFE VFI",
        "UpscaleModelLoader",
        "ImageUpscaleWithModel",
    ]
    object_info = await comfy_get("/object_info")
    node_checks = [{"name": name, "ok": name in object_info} for name in required_nodes]

    required_files = [
        active_base_dir / "models" / "diffusion_models" / "Wan2.2" / "wan2.2_ti2v_5B_fp16.safetensors",
        active_base_dir / "models" / "diffusion_models" / "Wan2.2" / "wan2.2_i2v_high_noise_14B_fp8_scaled.safetensors",
        active_base_dir / "models" / "diffusion_models" / "Wan2.2" / "wan2.2_i2v_low_noise_14B_fp8_scaled.safetensors",
        active_base_dir / "models" / "diffusion_models" / "Wan2.2" / "wan2.2_t2v_high_noise_14B_fp8_scaled.safetensors",
        active_base_dir / "models" / "diffusion_models" / "Wan2.2" / "wan2.2_t2v_low_noise_14B_fp8_scaled.safetensors",
        active_base_dir / "models" / "text_encoders" / "umt5_xxl_fp8_e4m3fn_scaled.safetensors",
        active_base_dir / "models" / "vae" / "wan_2.1_vae.safetensors",
        active_base_dir / "models" / "vae" / "wan2.2_vae.safetensors",
        active_base_dir / "models" / "checkpoints" / "ltx-video-2b-v0.9.5.safetensors",
        active_base_dir / "models" / "text_encoders" / "t5xxl_fp16.safetensors",
        active_base_dir / "models" / "loras" / "Wan2.2" / "wan2.2_i2v_lightx2v_4steps_lora_v1_high_noise.safetensors",
        active_base_dir / "models" / "loras" / "Wan2.2" / "wan2.2_i2v_lightx2v_4steps_lora_v1_low_noise.safetensors",
        active_base_dir / "models" / "loras" / "Wan2.2" / "wan2.2_t2v_lightx2v_4steps_lora_v1.1_high_noise.safetensors",
        active_base_dir / "models" / "loras" / "Wan2.2" / "wan2.2_t2v_lightx2v_4steps_lora_v1.1_low_noise.safetensors",
        active_base_dir / "custom_nodes" / "ComfyUI-Frame-Interpolation" / "ckpts" / "rife" / "rife49.pth",
        active_base_dir / "models" / "upscale_models" / "RealESRGAN_x2plus.pth",
        active_base_dir / "models" / "upscale_models" / "4x-UltraSharp.pth",
        input_dir / DEFAULT_IMAGE,
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
        "Mac LTX I2V": build_ltx_i2v_prompt(
            **{**sample_params, "width": 512, "height": 320, "length": 25, "steps": 12, "cfg": 3.0}
        ),
        "Mac LTX T2V": build_ltx_t2v_prompt(
            **{k: v for k, v in {**sample_params, "width": 512, "height": 320, "length": 25, "steps": 12, "cfg": 3.0}.items() if k != "image_name"}
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
        "paths": serializable_comfy_paths(comfy_paths),
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
    if mode in {"ltx_i2v", "ltx_t2v"}:
        length = normalize_ltx_frames(length)

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

    object_info: dict[str, Any] | None = None
    object_error = ""
    try:
        object_info = await comfy_get("/object_info")
        graph = resolve_graph_model_option_names(graph, object_info)
    except HTTPException as exc:
        object_error = str(exc.detail)
    graph_errors = validate_graph(graph)
    availability = await comfy_node_availability(graph)
    model_errors = graph_model_registry_errors(graph, object_info)
    try:
        system_stats = await comfy_get("/system_stats")
    except HTTPException:
        system_stats = None
    risk_checks = workflow_risk_checks(
        mode=mode,
        hardware=build_hardware_summary(system_stats),
        width=width,
        height=height,
        length=length,
    )
    output_nodes = [
        node_id
        for node_id, item in graph.items()
        if item.get("class_type") in {"SaveVideo", "VHS_VideoCombine", "SaveImage"}
    ]
    errors = list(graph_errors)
    errors.extend(model_errors)
    errors.extend(item["message"] for item in risk_checks if item.get("level") == "blocked")
    if availability["missing"]:
        errors.append(f"ComfyUI 缺少节点：{', '.join(availability['missing'])}")
    if availability["error"]:
        errors.append(availability["error"])
    if object_error and object_error not in errors:
        errors.append(object_error)
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
        "risk_checks": risk_checks,
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
    comfy_paths = await active_comfy_paths()
    mode = resolved["mode"]
    selected_label = resolved["label"]
    upscale_model = resolved["upscale_model"]
    rife_multiplier = clamp_int(resolved["rife_multiplier"], 2, 4)
    if mode in {"ltx_i2v", "ltx_t2v"}:
        length = normalize_ltx_frames(length)

    if mode in {"i2v_a14b", "ti2v_5b", "ltx_i2v"}:
        image_name = DEFAULT_IMAGE
        if image and image.filename:
            image_name = await save_upload(image, IMAGE_EXTENSIONS, comfy_paths)
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
    elif mode == "ltx_i2v":
        graph = build_ltx_i2v_prompt(
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
        title = selected_label or "Mac LTX I2V"
    elif mode == "ltx_t2v":
        graph = build_ltx_t2v_prompt(
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
        title = selected_label or "Mac LTX T2V"
    elif mode == "rife_2x":
        if source_video_filename:
            video_name = copy_media_to_input(
                source_video_filename,
                source_video_subfolder,
                source_video_type or "output",
                comfy_paths,
            )
        elif video and video.filename:
            video_name = await save_upload(video, VIDEO_EXTENSIONS, comfy_paths)
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
                comfy_paths,
            )
        elif video and video.filename:
            video_name = await save_upload(video, VIDEO_EXTENSIONS, comfy_paths)
            source_path = input_media_path(video_name, comfy_paths)
        else:
            raise HTTPException(status_code=400, detail="闪烁修复需要上传视频，或先完成上一阶段视频生成")

        media = run_deflicker(source_path, comfy_paths)
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
                comfy_paths,
            )
        elif video and video.filename:
            video_name = await save_upload(video, VIDEO_EXTENSIONS, comfy_paths)
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

    try:
        object_info = await comfy_get("/object_info")
    except HTTPException as exc:
        raise HTTPException(status_code=503, detail=f"ComfyUI 未连接，无法提交 workflow：{exc.detail}") from exc
    graph = resolve_graph_model_option_names(graph, object_info)
    graph_errors = validate_graph(graph)
    graph_errors.extend(graph_model_registry_errors(graph, object_info))
    missing_nodes = [name for name in graph_class_types(graph) if name not in object_info]
    if missing_nodes:
        graph_errors.append(f"ComfyUI 缺少节点：{', '.join(missing_nodes)}")
    try:
        system_stats = await comfy_get("/system_stats")
    except HTTPException:
        system_stats = None
    graph_errors.extend(
        item["message"]
        for item in workflow_risk_checks(
            mode=mode,
            hardware=build_hardware_summary(system_stats),
            width=width,
            height=height,
            length=length,
        )
        if item.get("level") == "blocked"
    )
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
    path = safe_media_path(filename, subfolder, type, await active_comfy_paths())
    media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return FileResponse(path, media_type=media_type, filename=path.name)


@app.exception_handler(HTTPException)
async def http_exception_handler(_: Any, exc: HTTPException) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
