from __future__ import annotations

import argparse
import binascii
import os
import shutil
import struct
import subprocess
import sys
import time
import urllib.error
import urllib.request
import zlib
from pathlib import Path


USER_AGENT = "wan22-local-video-workflow/1.0"


MODEL_FILES = [
    {
        "group": "wan5b",
        "name": "wan2.2_ti2v_5B_fp16.safetensors",
        "url": "https://huggingface.co/Comfy-Org/Wan_2.2_ComfyUI_Repackaged/resolve/main/split_files/diffusion_models/wan2.2_ti2v_5B_fp16.safetensors",
        "dest": "models/diffusion_models/Wan2.2/wan2.2_ti2v_5B_fp16.safetensors",
        "bytes": 9999658848,
    },
    {
        "group": "a14b_i2v",
        "name": "wan2.2_i2v_high_noise_14B_fp8_scaled.safetensors",
        "url": "https://huggingface.co/Comfy-Org/Wan_2.2_ComfyUI_Repackaged/resolve/main/split_files/diffusion_models/wan2.2_i2v_high_noise_14B_fp8_scaled.safetensors",
        "dest": "models/diffusion_models/Wan2.2/wan2.2_i2v_high_noise_14B_fp8_scaled.safetensors",
        "bytes": 14294742832,
    },
    {
        "group": "a14b_i2v",
        "name": "wan2.2_i2v_low_noise_14B_fp8_scaled.safetensors",
        "url": "https://huggingface.co/Comfy-Org/Wan_2.2_ComfyUI_Repackaged/resolve/main/split_files/diffusion_models/wan2.2_i2v_low_noise_14B_fp8_scaled.safetensors",
        "dest": "models/diffusion_models/Wan2.2/wan2.2_i2v_low_noise_14B_fp8_scaled.safetensors",
        "bytes": 14294742832,
    },
    {
        "group": "t2v",
        "name": "wan2.2_t2v_high_noise_14B_fp8_scaled.safetensors",
        "url": "https://huggingface.co/Comfy-Org/Wan_2.2_ComfyUI_Repackaged/resolve/main/split_files/diffusion_models/wan2.2_t2v_high_noise_14B_fp8_scaled.safetensors",
        "dest": "models/diffusion_models/Wan2.2/wan2.2_t2v_high_noise_14B_fp8_scaled.safetensors",
        "bytes": 14293923632,
    },
    {
        "group": "t2v",
        "name": "wan2.2_t2v_low_noise_14B_fp8_scaled.safetensors",
        "url": "https://huggingface.co/Comfy-Org/Wan_2.2_ComfyUI_Repackaged/resolve/main/split_files/diffusion_models/wan2.2_t2v_low_noise_14B_fp8_scaled.safetensors",
        "dest": "models/diffusion_models/Wan2.2/wan2.2_t2v_low_noise_14B_fp8_scaled.safetensors",
        "bytes": 14293923632,
    },
    {
        "group": "wan_shared",
        "name": "umt5_xxl_fp8_e4m3fn_scaled.safetensors",
        "url": "https://huggingface.co/Comfy-Org/Wan_2.1_ComfyUI_repackaged/resolve/main/split_files/text_encoders/umt5_xxl_fp8_e4m3fn_scaled.safetensors",
        "dest": "models/text_encoders/umt5_xxl_fp8_e4m3fn_scaled.safetensors",
        "bytes": 6735906897,
    },
    {
        "group": "a14b_shared",
        "name": "wan_2.1_vae.safetensors",
        "url": "https://huggingface.co/Comfy-Org/Wan_2.2_ComfyUI_Repackaged/resolve/main/split_files/vae/wan_2.1_vae.safetensors",
        "dest": "models/vae/wan_2.1_vae.safetensors",
        "bytes": 253815318,
    },
    {
        "group": "wan5b",
        "name": "wan2.2_vae.safetensors",
        "url": "https://huggingface.co/Comfy-Org/Wan_2.2_ComfyUI_Repackaged/resolve/main/split_files/vae/wan2.2_vae.safetensors",
        "dest": "models/vae/wan2.2_vae.safetensors",
        "bytes": 1409400960,
    },
    {
        "group": "ltx",
        "name": "ltx-video-2b-v0.9.5.safetensors",
        "url": "https://huggingface.co/Lightricks/LTX-Video/resolve/main/ltx-video-2b-v0.9.5.safetensors",
        "dest": "models/checkpoints/ltx-video-2b-v0.9.5.safetensors",
        "bytes": 6340729500,
    },
    {
        "group": "ltx",
        "name": "t5xxl_fp16.safetensors",
        "url": "https://huggingface.co/comfyanonymous/flux_text_encoders/resolve/main/t5xxl_fp16.safetensors",
        "dest": "models/text_encoders/t5xxl_fp16.safetensors",
        "bytes": 9787841024,
    },
    {
        "group": "lora",
        "name": "wan2.2_i2v_lightx2v_4steps_lora_v1_high_noise.safetensors",
        "url": "https://huggingface.co/Comfy-Org/Wan_2.2_ComfyUI_Repackaged/resolve/main/split_files/loras/wan2.2_i2v_lightx2v_4steps_lora_v1_high_noise.safetensors",
        "dest": "models/loras/Wan2.2/wan2.2_i2v_lightx2v_4steps_lora_v1_high_noise.safetensors",
        "bytes": 1226977424,
    },
    {
        "group": "lora",
        "name": "wan2.2_i2v_lightx2v_4steps_lora_v1_low_noise.safetensors",
        "url": "https://huggingface.co/Comfy-Org/Wan_2.2_ComfyUI_Repackaged/resolve/main/split_files/loras/wan2.2_i2v_lightx2v_4steps_lora_v1_low_noise.safetensors",
        "dest": "models/loras/Wan2.2/wan2.2_i2v_lightx2v_4steps_lora_v1_low_noise.safetensors",
        "bytes": 1226977424,
    },
    {
        "group": "lora",
        "name": "wan2.2_t2v_lightx2v_4steps_lora_v1.1_high_noise.safetensors",
        "url": "https://huggingface.co/Comfy-Org/Wan_2.2_ComfyUI_Repackaged/resolve/main/split_files/loras/wan2.2_t2v_lightx2v_4steps_lora_v1.1_high_noise.safetensors",
        "dest": "models/loras/Wan2.2/wan2.2_t2v_lightx2v_4steps_lora_v1.1_high_noise.safetensors",
        "bytes": 1226977424,
    },
    {
        "group": "lora",
        "name": "wan2.2_t2v_lightx2v_4steps_lora_v1.1_low_noise.safetensors",
        "url": "https://huggingface.co/Comfy-Org/Wan_2.2_ComfyUI_Repackaged/resolve/main/split_files/loras/wan2.2_t2v_lightx2v_4steps_lora_v1.1_low_noise.safetensors",
        "dest": "models/loras/Wan2.2/wan2.2_t2v_lightx2v_4steps_lora_v1.1_low_noise.safetensors",
        "bytes": 1226977424,
    },
    {
        "group": "post",
        "name": "rife49.pth",
        "url": "https://huggingface.co/marduk191/rife/resolve/main/rife49.pth",
        "dest": "custom_nodes/ComfyUI-Frame-Interpolation/ckpts/rife/rife49.pth",
        "bytes": 21345274,
    },
    {
        "group": "post",
        "name": "RealESRGAN_x2plus.pth",
        "url": "https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.1/RealESRGAN_x2plus.pth",
        "dest": "models/upscale_models/RealESRGAN_x2plus.pth",
        "bytes": 67061725,
    },
    {
        "group": "post",
        "name": "4x-UltraSharp.pth",
        "url": "https://huggingface.co/lokCX/4x-Ultrasharp/resolve/main/4x-UltraSharp.pth",
        "dest": "models/upscale_models/4x-UltraSharp.pth",
        "bytes": 66961958,
    },
]


CUSTOM_NODE_REPOS = [
    {
        "name": "ComfyUI-VideoHelperSuite",
        "url": "https://github.com/Kosinkadink/ComfyUI-VideoHelperSuite.git",
        "dest": "custom_nodes/ComfyUI-VideoHelperSuite",
    },
    {
        "name": "ComfyUI-Frame-Interpolation",
        "url": "https://github.com/Fannovel16/ComfyUI-Frame-Interpolation.git",
        "dest": "custom_nodes/ComfyUI-Frame-Interpolation",
    },
]


PROFILE_GROUPS = {
    "cuda-full": {"wan5b", "a14b_i2v", "t2v", "wan_shared", "a14b_shared", "lora", "post"},
    "mac-low": {"ltx"},
    "mac-balanced": {"ltx"},
    "mac-wan5b": {"ltx", "wan5b", "wan_shared"},
    "post-only": {"post"},
}

PROFILE_CUSTOM_NODES = {
    "cuda-full": True,
    "mac-low": False,
    "mac-balanced": False,
    "mac-wan5b": False,
    "post-only": True,
}


def log(message: str) -> None:
    print(message, flush=True)


def should_skip(item: dict[str, object], profile: str, skip_t2v: bool, skip_loras: bool) -> bool:
    group = item["group"]
    allowed_groups = PROFILE_GROUPS.get(profile, PROFILE_GROUPS["cuda-full"])
    return (
        group not in allowed_groups
        or (skip_t2v and group == "t2v")
        or (skip_loras and group == "lora")
    )


def file_state(path: Path, expected_bytes: int) -> str:
    if not path.exists():
        return "missing"
    size = path.stat().st_size
    if size == expected_bytes:
        return "complete"
    if size > expected_bytes:
        return "too_large"
    return "partial"


def download_file(url: str, dest: Path, expected_bytes: int, retries: int = 8) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    state = file_state(dest, expected_bytes)
    if state == "complete":
        log(f"[skip] {dest.name}")
        return
    if state == "too_large":
        raise RuntimeError(f"Existing file is larger than expected: {dest}")

    for attempt in range(1, retries + 1):
        existing = dest.stat().st_size if dest.exists() else 0
        headers = {"User-Agent": USER_AGENT}
        mode = "ab" if existing else "wb"
        if existing:
            headers["Range"] = f"bytes={existing}-"
            log(f"[resume] {dest.name} ({existing} / {expected_bytes} bytes)")
        else:
            log(f"[download] {dest.name}")

        request = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                if existing and response.status == 200:
                    log(f"[restart] server ignored resume for {dest.name}")
                    existing = 0
                    mode = "wb"
                last_report = time.time()
                with dest.open(mode) as output:
                    while True:
                        chunk = response.read(1024 * 1024 * 8)
                        if not chunk:
                            break
                        output.write(chunk)
                        now = time.time()
                        if now - last_report > 20:
                            size = dest.stat().st_size
                            pct = min(100.0, size / expected_bytes * 100)
                            log(f"[progress] {dest.name}: {pct:.1f}%")
                            last_report = now
        except (urllib.error.URLError, TimeoutError) as exc:
            if attempt == retries:
                raise
            wait = min(30, 3 * attempt)
            log(f"[retry] {dest.name}: {exc} (wait {wait}s)")
            time.sleep(wait)
            continue

        actual = dest.stat().st_size
        if actual == expected_bytes:
            log(f"[ok] {dest.name}")
            return
        if actual > expected_bytes:
            raise RuntimeError(f"Size check failed for {dest.name}: got {actual}, expected {expected_bytes}")
        log(f"[retry] incomplete {dest.name}: got {actual}, expected {expected_bytes}")

    raise RuntimeError(f"Could not complete download: {dest.name}")


def run(command: list[str], cwd: Path) -> None:
    log(f"[run] {' '.join(command)}")
    subprocess.run(command, cwd=str(cwd), check=True)


def install_node_requirements(requirements: Path, comfy_python: Path | None) -> None:
    if comfy_python and comfy_python.exists():
        run([str(comfy_python), "-m", "pip", "install", "-r", str(requirements)], requirements.parent)
        return
    log(
        f"[warn] {requirements.parent.name} has requirements.txt, but ComfyUI Python was not found. "
        "Install these requirements in the ComfyUI Python environment, then restart ComfyUI."
    )


def ensure_custom_nodes(
    base_dir: Path,
    *,
    comfy_python: Path | None = None,
    install_requirements: bool = True,
) -> None:
    custom_nodes_dir = base_dir / "custom_nodes"
    custom_nodes_dir.mkdir(parents=True, exist_ok=True)
    git = shutil.which("git")

    for repo in CUSTOM_NODE_REPOS:
        dest = base_dir / str(repo["dest"])
        if dest.exists():
            log(f"[skip] {repo['name']}")
        else:
            if not git:
                raise RuntimeError("git is required to install missing ComfyUI custom nodes.")
            run([git, "clone", "--depth", "1", str(repo["url"]), str(dest)], custom_nodes_dir)
        requirements = dest / "requirements.txt"
        if install_requirements and requirements.exists():
            install_node_requirements(requirements, comfy_python)


def png_chunk(chunk_type: bytes, data: bytes) -> bytes:
    return (
        struct.pack(">I", len(data))
        + chunk_type
        + data
        + struct.pack(">I", binascii.crc32(chunk_type + data) & 0xFFFFFFFF)
    )


def write_sample_png(path: Path, width: int = 1280, height: int = 704) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        log(f"[skip] {path.name}")
        return

    raw = bytearray()
    for y in range(height):
        raw.append(0)
        for x in range(width):
            vignette = int(34 * (1 - abs((x / width) - 0.5) * 1.2))
            rgb_wave = int(42 * max(0, 1 - abs(y - height * 0.28) / (height * 0.26)))
            desk = 36 if height * 0.58 < y < height * 0.76 else 0
            screen = 55 if width * 0.32 < x < width * 0.68 and height * 0.25 < y < height * 0.52 else 0
            r = min(255, 18 + vignette + screen // 3 + desk)
            g = min(255, 32 + vignette + rgb_wave + screen)
            b = min(255, 45 + vignette + rgb_wave * 2 + screen + desk // 2)
            raw.extend((r, g, b))

    data = b"".join(
        [
            b"\x89PNG\r\n\x1a\n",
            png_chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)),
            png_chunk(b"IDAT", zlib.compress(bytes(raw), 6)),
            png_chunk(b"IEND", b""),
        ]
    )
    path.write_bytes(data)
    log(f"[ok] generated {path.name}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Install/check Wan2.2 local video workflow assets.")
    parser.add_argument("--base-dir", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument(
        "--profile",
        choices=sorted(PROFILE_GROUPS),
        default="cuda-full",
        help="Asset profile to install: CUDA Wan mainline, Mac LTX, Mac Wan5B, or post tools.",
    )
    parser.add_argument("--comfy-python", type=Path, default=None, help="Python executable used by ComfyUI for custom node requirements.")
    parser.add_argument("--skip-t2v", action="store_true", help="Skip optional T2V A14B files.")
    parser.add_argument("--skip-loras", action="store_true", help="Skip 4-step acceleration LoRAs.")
    parser.add_argument("--no-custom-nodes", action="store_true", help="Do not clone missing ComfyUI custom node repos.")
    parser.add_argument("--no-node-requirements", action="store_true", help="Do not install custom node requirements.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    base_dir = args.base_dir.resolve()
    log(f"[base] {base_dir}")
    log(f"[profile] {args.profile}")

    comfy_python = args.comfy_python.resolve() if args.comfy_python else None
    if comfy_python:
        log(f"[comfy-python] {comfy_python}")

    if not args.no_custom_nodes and PROFILE_CUSTOM_NODES.get(args.profile, True):
        ensure_custom_nodes(
            base_dir,
            comfy_python=comfy_python,
            install_requirements=not args.no_node_requirements,
        )
    elif PROFILE_CUSTOM_NODES.get(args.profile, True) is False:
        log(f"[skip] custom nodes are not required for profile {args.profile}")

    for item in MODEL_FILES:
        if should_skip(item, args.profile, args.skip_t2v, args.skip_loras):
            continue
        download_file(str(item["url"]), base_dir / str(item["dest"]), int(item["bytes"]))

    write_sample_png(base_dir / "input" / "wan22_sample_esports_keyframe.png")
    log("Wan2.2 workflow assets are installed and verified.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        log("Interrupted.")
        raise
    except Exception as exc:
        log(f"[error] {exc}")
        raise SystemExit(1)
