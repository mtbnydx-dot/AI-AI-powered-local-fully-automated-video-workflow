from __future__ import annotations

import argparse
import binascii
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
import zlib
from pathlib import Path
from urllib.parse import urlparse, urlunparse


USER_AGENT = "wan22-local-video-workflow/1.0"
SAFETY_BUFFER_BYTES = 10 * 1024**3
HF_ENDPOINT_ENV_NAMES = ("WAN22_HF_ENDPOINT", "HF_ENDPOINT")
LOCAL_ASSET_ENV_NAMES = ("WAN22_LOCAL_ASSET_DIRS", "WAN22_MODEL_CACHE_DIRS")


MODEL_FILES = [
    {
        "id": "ti2v_5b",
        "group": "wan5b",
        "name": "wan2.2_ti2v_5B_fp16.safetensors",
        "url": "https://huggingface.co/Comfy-Org/Wan_2.2_ComfyUI_Repackaged/resolve/main/split_files/diffusion_models/wan2.2_ti2v_5B_fp16.safetensors",
        "dest": "models/diffusion_models/Wan2.2/wan2.2_ti2v_5B_fp16.safetensors",
        "bytes": 9999658848,
    },
    {
        "id": "i2v_high",
        "group": "a14b_i2v",
        "name": "wan2.2_i2v_high_noise_14B_fp8_scaled.safetensors",
        "url": "https://huggingface.co/Comfy-Org/Wan_2.2_ComfyUI_Repackaged/resolve/main/split_files/diffusion_models/wan2.2_i2v_high_noise_14B_fp8_scaled.safetensors",
        "dest": "models/diffusion_models/Wan2.2/wan2.2_i2v_high_noise_14B_fp8_scaled.safetensors",
        "bytes": 14294742832,
    },
    {
        "id": "i2v_low",
        "group": "a14b_i2v",
        "name": "wan2.2_i2v_low_noise_14B_fp8_scaled.safetensors",
        "url": "https://huggingface.co/Comfy-Org/Wan_2.2_ComfyUI_Repackaged/resolve/main/split_files/diffusion_models/wan2.2_i2v_low_noise_14B_fp8_scaled.safetensors",
        "dest": "models/diffusion_models/Wan2.2/wan2.2_i2v_low_noise_14B_fp8_scaled.safetensors",
        "bytes": 14294742832,
    },
    {
        "id": "t2v_high",
        "group": "t2v",
        "name": "wan2.2_t2v_high_noise_14B_fp8_scaled.safetensors",
        "url": "https://huggingface.co/Comfy-Org/Wan_2.2_ComfyUI_Repackaged/resolve/main/split_files/diffusion_models/wan2.2_t2v_high_noise_14B_fp8_scaled.safetensors",
        "dest": "models/diffusion_models/Wan2.2/wan2.2_t2v_high_noise_14B_fp8_scaled.safetensors",
        "bytes": 14293923632,
    },
    {
        "id": "t2v_low",
        "group": "t2v",
        "name": "wan2.2_t2v_low_noise_14B_fp8_scaled.safetensors",
        "url": "https://huggingface.co/Comfy-Org/Wan_2.2_ComfyUI_Repackaged/resolve/main/split_files/diffusion_models/wan2.2_t2v_low_noise_14B_fp8_scaled.safetensors",
        "dest": "models/diffusion_models/Wan2.2/wan2.2_t2v_low_noise_14B_fp8_scaled.safetensors",
        "bytes": 14293923632,
    },
    {
        "id": "umt5",
        "group": "wan_shared",
        "name": "umt5_xxl_fp8_e4m3fn_scaled.safetensors",
        "url": "https://huggingface.co/Comfy-Org/Wan_2.1_ComfyUI_repackaged/resolve/main/split_files/text_encoders/umt5_xxl_fp8_e4m3fn_scaled.safetensors",
        "dest": "models/text_encoders/umt5_xxl_fp8_e4m3fn_scaled.safetensors",
        "bytes": 6735906897,
    },
    {
        "id": "wan21_vae",
        "group": "a14b_shared",
        "name": "wan_2.1_vae.safetensors",
        "url": "https://huggingface.co/Comfy-Org/Wan_2.2_ComfyUI_Repackaged/resolve/main/split_files/vae/wan_2.1_vae.safetensors",
        "dest": "models/vae/wan_2.1_vae.safetensors",
        "bytes": 253815318,
    },
    {
        "id": "wan22_vae",
        "group": "wan5b",
        "name": "wan2.2_vae.safetensors",
        "url": "https://huggingface.co/Comfy-Org/Wan_2.2_ComfyUI_Repackaged/resolve/main/split_files/vae/wan2.2_vae.safetensors",
        "dest": "models/vae/wan2.2_vae.safetensors",
        "bytes": 1409400960,
    },
    {
        "id": "ltx_2b_095",
        "group": "ltx",
        "name": "ltx-video-2b-v0.9.5.safetensors",
        "url": "https://huggingface.co/Lightricks/LTX-Video/resolve/main/ltx-video-2b-v0.9.5.safetensors",
        "dest": "models/checkpoints/ltx-video-2b-v0.9.5.safetensors",
        "bytes": 6340729500,
    },
    {
        "id": "t5xxl_fp16",
        "group": "ltx",
        "name": "t5xxl_fp16.safetensors",
        "url": "https://huggingface.co/comfyanonymous/flux_text_encoders/resolve/main/t5xxl_fp16.safetensors",
        "dest": "models/text_encoders/t5xxl_fp16.safetensors",
        "bytes": 9787841024,
    },
    {
        "id": "i2v_lora_high",
        "group": "lora",
        "name": "wan2.2_i2v_lightx2v_4steps_lora_v1_high_noise.safetensors",
        "url": "https://huggingface.co/Comfy-Org/Wan_2.2_ComfyUI_Repackaged/resolve/main/split_files/loras/wan2.2_i2v_lightx2v_4steps_lora_v1_high_noise.safetensors",
        "dest": "models/loras/Wan2.2/wan2.2_i2v_lightx2v_4steps_lora_v1_high_noise.safetensors",
        "bytes": 1226977424,
    },
    {
        "id": "i2v_lora_low",
        "group": "lora",
        "name": "wan2.2_i2v_lightx2v_4steps_lora_v1_low_noise.safetensors",
        "url": "https://huggingface.co/Comfy-Org/Wan_2.2_ComfyUI_Repackaged/resolve/main/split_files/loras/wan2.2_i2v_lightx2v_4steps_lora_v1_low_noise.safetensors",
        "dest": "models/loras/Wan2.2/wan2.2_i2v_lightx2v_4steps_lora_v1_low_noise.safetensors",
        "bytes": 1226977424,
    },
    {
        "id": "t2v_lora_high",
        "group": "lora",
        "name": "wan2.2_t2v_lightx2v_4steps_lora_v1.1_high_noise.safetensors",
        "url": "https://huggingface.co/Comfy-Org/Wan_2.2_ComfyUI_Repackaged/resolve/main/split_files/loras/wan2.2_t2v_lightx2v_4steps_lora_v1.1_high_noise.safetensors",
        "dest": "models/loras/Wan2.2/wan2.2_t2v_lightx2v_4steps_lora_v1.1_high_noise.safetensors",
        "bytes": 1226977424,
    },
    {
        "id": "t2v_lora_low",
        "group": "lora",
        "name": "wan2.2_t2v_lightx2v_4steps_lora_v1.1_low_noise.safetensors",
        "url": "https://huggingface.co/Comfy-Org/Wan_2.2_ComfyUI_Repackaged/resolve/main/split_files/loras/wan2.2_t2v_lightx2v_4steps_lora_v1.1_low_noise.safetensors",
        "dest": "models/loras/Wan2.2/wan2.2_t2v_lightx2v_4steps_lora_v1.1_low_noise.safetensors",
        "bytes": 1226977424,
    },
    {
        "id": "rife49",
        "group": "post",
        "name": "rife49.pth",
        "url": "https://huggingface.co/marduk191/rife/resolve/main/rife49.pth",
        "dest": "custom_nodes/ComfyUI-Frame-Interpolation/ckpts/rife/rife49.pth",
        "bytes": 21345274,
    },
    {
        "id": "realesrgan_x2",
        "group": "post",
        "name": "RealESRGAN_x2plus.pth",
        "url": "https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.1/RealESRGAN_x2plus.pth",
        "dest": "models/upscale_models/RealESRGAN_x2plus.pth",
        "bytes": 67061725,
    },
    {
        "id": "ultrasharp_x4",
        "group": "post",
        "name": "4x-UltraSharp.pth",
        "url": "https://huggingface.co/lokCX/4x-Ultrasharp/resolve/main/4x-UltraSharp.pth",
        "dest": "models/upscale_models/4x-UltraSharp.pth",
        "bytes": 66961958,
    },
]


CUSTOM_NODE_REPOS = [
    {
        "id": "video_helper_suite",
        "name": "ComfyUI-VideoHelperSuite",
        "url": "https://github.com/Kosinkadink/ComfyUI-VideoHelperSuite.git",
        "dest": "custom_nodes/ComfyUI-VideoHelperSuite",
    },
    {
        "id": "frame_interpolation",
        "name": "ComfyUI-Frame-Interpolation",
        "url": "https://github.com/Fannovel16/ComfyUI-Frame-Interpolation.git",
        "dest": "custom_nodes/ComfyUI-Frame-Interpolation",
    },
]


def normalize_hf_endpoint(value: str | None) -> str:
    value = (value or "").strip().rstrip("/")
    if not value:
        return ""
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("HF endpoint must be an http(s) URL, for example https://huggingface.co")
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", "", ""))


def configured_hf_endpoint() -> str:
    for name in HF_ENDPOINT_ENV_NAMES:
        endpoint = normalize_hf_endpoint(os.environ.get(name))
        if endpoint:
            return endpoint
    return ""


def effective_download_url(url: str, hf_endpoint: str | None = None) -> str:
    endpoint = normalize_hf_endpoint(hf_endpoint) if hf_endpoint is not None else configured_hf_endpoint()
    if not endpoint:
        return url
    parsed = urlparse(url)
    if parsed.hostname != "huggingface.co":
        return url
    mirror = urlparse(endpoint)
    mirror_path = mirror.path.rstrip("/")
    path = f"{mirror_path}{parsed.path}" if mirror_path else parsed.path
    return urlunparse((mirror.scheme, mirror.netloc, path, "", parsed.query, parsed.fragment))


PROFILE_GROUPS = {
    "cuda-full": {"wan5b", "a14b_i2v", "t2v", "wan_shared", "a14b_shared", "lora", "post"},
    "cuda-wan5b": {"wan5b", "wan_shared", "post"},
    "mac-low": {"ltx", "post"},
    "mac-balanced": {"ltx", "post"},
    "mac-wan5b": {"ltx", "wan5b", "wan_shared", "post"},
    "post-only": {"post"},
}

PROFILE_CUSTOM_NODES = {
    "cuda-full": True,
    "cuda-wan5b": True,
    "mac-low": True,
    "mac-balanced": True,
    "mac-wan5b": True,
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


def selected_model_items(profile: str, skip_t2v: bool = False, skip_loras: bool = False) -> list[dict[str, object]]:
    return [
        item
        for item in MODEL_FILES
        if not should_skip(item, profile, skip_t2v=skip_t2v, skip_loras=skip_loras)
    ]


def file_state(path: Path, expected_bytes: int) -> str:
    if not path.exists():
        return "missing"
    size = path.stat().st_size
    if size == expected_bytes:
        return "complete"
    if size > expected_bytes:
        return "too_large"
    return "partial"


def looks_like_asset_root(path: Path) -> bool:
    return path.exists() and path.is_dir() and any((path / name).exists() for name in ("models", "custom_nodes", "input"))


def unique_paths(paths: list[Path]) -> list[Path]:
    unique: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        try:
            resolved = path.expanduser().resolve()
        except Exception:
            continue
        key = str(resolved).lower() if os.name == "nt" else str(resolved)
        if key in seen:
            continue
        seen.add(key)
        unique.append(resolved)
    return unique


def env_local_asset_roots() -> list[Path]:
    roots: list[Path] = []
    for name in LOCAL_ASSET_ENV_NAMES:
        value = os.environ.get(name) or ""
        for part in value.split(os.pathsep):
            part = part.strip().strip('"')
            if part:
                roots.append(Path(part))
    return roots


def local_asset_roots(base_dir: Path) -> list[Path]:
    script_root = Path(__file__).resolve().parents[1]
    candidates = env_local_asset_roots()
    for candidate in (script_root.parent, Path.cwd().parent, base_dir.parent):
        if candidate != base_dir and looks_like_asset_root(candidate):
            candidates.append(candidate)
    return unique_paths(candidates)


def candidate_paths_for_item(root: Path, item: dict[str, object]) -> list[Path]:
    dest = Path(str(item["dest"]))
    paths = [root / dest]
    parts = dest.parts
    if parts and parts[0] in {"models", "custom_nodes", "input"}:
        paths.append(root / Path(*parts[1:]))
    paths.append(root / str(item["name"]))
    return unique_paths(paths)


def find_local_asset_candidate(
    item: dict[str, object],
    dest: Path | None = None,
    base_dir: Path | None = None,
) -> Path | None:
    expected = int(item["bytes"])
    name = str(item["name"])
    base_dir = (base_dir or Path.cwd()).expanduser().resolve()
    dest_resolved = dest.expanduser().resolve() if dest else None
    for root in local_asset_roots(base_dir):
        for candidate in candidate_paths_for_item(root, item):
            if dest_resolved and candidate == dest_resolved:
                continue
            if candidate.is_file() and candidate.stat().st_size == expected:
                return candidate
        try:
            matches = root.rglob(name)
            for candidate in matches:
                if dest_resolved and candidate.resolve() == dest_resolved:
                    continue
                if candidate.is_file() and candidate.stat().st_size == expected:
                    return candidate.resolve()
        except (OSError, PermissionError):
            continue
    return None


def install_from_local_asset(item: dict[str, object], dest: Path, expected_bytes: int, base_dir: Path) -> bool:
    candidate = find_local_asset_candidate(item, dest=dest, base_dir=base_dir)
    if not candidate:
        return False
    dest.parent.mkdir(parents=True, exist_ok=True)
    partial = dest.with_name(dest.name + ".part")
    partial.unlink(missing_ok=True)
    if dest.exists():
        state = file_state(dest, expected_bytes)
        if state == "complete":
            log(f"[skip] {dest.name}")
            return True
        dest.unlink()
    log(f"[local] {dest.name} <- {candidate}")
    force_copy = os.environ.get("WAN22_COPY_LOCAL_ASSETS") == "1"
    if not force_copy:
        try:
            os.link(candidate, dest)
        except OSError:
            shutil.copy2(candidate, dest)
    else:
        shutil.copy2(candidate, dest)
    actual = dest.stat().st_size
    if actual != expected_bytes:
        raise RuntimeError(f"Local asset size check failed for {dest.name}: got {actual}, expected {expected_bytes}")
    return True


def nearest_existing_path(path: Path) -> Path:
    resolved = path.expanduser().resolve()
    if resolved.exists():
        return resolved
    for parent in resolved.parents:
        if parent.exists():
            return parent
    return Path.cwd()


def remaining_bytes_for_item(base_dir: Path, item: dict[str, object]) -> int:
    expected = int(item["bytes"])
    dest = base_dir / str(item["dest"])
    if dest.exists():
        size = dest.stat().st_size
        if size >= expected:
            return 0
        return expected - size
    if find_local_asset_candidate(item, dest=dest, base_dir=base_dir):
        return 0

    partial = dest.with_name(dest.name + ".part")
    if partial.exists():
        size = min(partial.stat().st_size, expected)
        return expected - size
    return expected


def planned_download_bytes(base_dir: Path, items: list[dict[str, object]]) -> int:
    return sum(remaining_bytes_for_item(base_dir, item) for item in items)


def bytes_to_gb(value: int | float) -> float:
    return round(float(value) / 1024**3, 2)


def gb_to_bytes(value: float) -> int:
    return int(float(value) * 1024**3)


def disk_space_plan(base_dir: Path, required_bytes: int, buffer_bytes: int = SAFETY_BUFFER_BYTES) -> dict[str, object]:
    usage_path = nearest_existing_path(base_dir)
    usage = shutil.disk_usage(usage_path)
    required_with_buffer = required_bytes + (buffer_bytes if required_bytes > 0 else 0)
    return {
        "path": str(usage_path),
        "required_bytes": int(required_bytes),
        "required_gb": bytes_to_gb(required_bytes),
        "buffer_bytes": int(buffer_bytes if required_bytes > 0 else 0),
        "buffer_gb": bytes_to_gb(buffer_bytes if required_bytes > 0 else 0),
        "recommended_free_bytes": int(required_with_buffer),
        "recommended_free_gb": bytes_to_gb(required_with_buffer),
        "free_bytes": int(usage.free),
        "free_gb": bytes_to_gb(usage.free),
        "total_bytes": int(usage.total),
        "total_gb": bytes_to_gb(usage.total),
        "ok": bool(required_bytes == 0 or usage.free >= required_with_buffer),
    }


def log_disk_plan(plan: dict[str, object]) -> None:
    log(
        "[disk] "
        f"free {plan['free_gb']} GB, "
        f"required {plan['required_gb']} GB, "
        f"recommended free {plan['recommended_free_gb']} GB at {plan['path']}"
    )


def download_file(url: str, dest: Path, expected_bytes: int, retries: int = 8) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    partial = dest.with_name(dest.name + ".part")
    state = file_state(dest, expected_bytes)
    if state == "complete":
        partial.unlink(missing_ok=True)
        log(f"[skip] {dest.name}")
        return
    if state == "too_large":
        raise RuntimeError(f"Existing file is larger than expected: {dest}")
    if state == "partial":
        if partial.exists():
            dest.unlink()
        else:
            dest.replace(partial)
    if partial.exists():
        partial_size = partial.stat().st_size
        if partial_size == expected_bytes:
            partial.replace(dest)
            log(f"[ok] {dest.name} (finalized existing .part)")
            return
        if partial_size > expected_bytes:
            raise RuntimeError(f"Partial file is larger than expected: {partial}")

    for attempt in range(1, retries + 1):
        existing = partial.stat().st_size if partial.exists() else 0
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
                with partial.open(mode) as output:
                    while True:
                        chunk = response.read(1024 * 1024 * 8)
                        if not chunk:
                            break
                        output.write(chunk)
                        now = time.time()
                        if now - last_report > 20:
                            size = partial.stat().st_size
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

        actual = partial.stat().st_size if partial.exists() else 0
        if actual == expected_bytes:
            partial.replace(dest)
            log(f"[ok] {dest.name}")
            return
        if actual > expected_bytes:
            raise RuntimeError(f"Size check failed for {dest.name}: got {actual}, expected {expected_bytes}")
        log(f"[retry] incomplete {dest.name}: got {actual}, expected {expected_bytes}")

    raise RuntimeError(f"Could not complete download: {dest.name}")


def run(command: list[str], cwd: Path) -> None:
    log(f"[run] {' '.join(command)}")
    subprocess.run(command, cwd=str(cwd), check=True)


def run_optional(command: list[str], cwd: Path) -> None:
    log(f"[run] {' '.join(command)}")
    result = subprocess.run(command, cwd=str(cwd))
    if result.returncode != 0:
        log(f"[warn] command failed with {result.returncode}; continuing")


def ensure_pip_available(python: Path, cwd: Path) -> None:
    try:
        subprocess.run(
            [str(python), "-m", "pip", "--version"],
            cwd=str(cwd),
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=30,
        )
        return
    except Exception:
        log("[pip] pip is not ready in the ComfyUI environment; running ensurepip")
    run([str(python), "-m", "ensurepip", "--upgrade"], cwd)
    subprocess.run(
        [str(python), "-m", "pip", "--version"],
        cwd=str(cwd),
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=30,
    )


def run_pip(command: list[str], cwd: Path, attempts: int = 3) -> None:
    last_error: subprocess.CalledProcessError | None = None
    for attempt in range(1, attempts + 1):
        try:
            run(command, cwd)
            return
        except subprocess.CalledProcessError as exc:
            last_error = exc
            if attempt >= attempts:
                break
            wait = min(20, 3 * attempt)
            log(f"[retry] pip command failed with {exc.returncode}; wait {wait}s")
            time.sleep(wait)
    assert last_error is not None
    raise last_error


def github_zip_candidates(repo_url: str) -> list[str]:
    base = repo_url.removesuffix(".git")
    return [
        f"{base}/archive/refs/heads/main.zip",
        f"{base}/archive/refs/heads/master.zip",
    ]


def download_zip(urls: list[str], dest: Path, retries: int = 3) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    partial = dest.with_name(dest.name + ".part")
    last_error: Exception | None = None
    for url in urls:
        for attempt in range(1, retries + 1):
            request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            try:
                log(f"[download] {url}")
                with urllib.request.urlopen(request, timeout=90) as response, partial.open("wb") as output:
                    shutil.copyfileobj(response, output)
                partial.replace(dest)
                return
            except (urllib.error.URLError, TimeoutError) as exc:
                last_error = exc
                partial.unlink(missing_ok=True)
                if attempt == retries:
                    log(f"[warn] zip download failed: {exc}")
                    break
                wait = min(15, 2 * attempt)
                log(f"[retry] zip download failed: {exc} (wait {wait}s)")
                time.sleep(wait)
    raise RuntimeError(f"Could not download custom node zip: {last_error}")


def extract_single_root_zip(zip_path: Path, dest: Path) -> None:
    with tempfile.TemporaryDirectory(prefix="comfy_node_zip_") as temp_name:
        temp_dir = Path(temp_name)
        with zipfile.ZipFile(zip_path) as archive:
            archive.extractall(temp_dir)
        roots = [item for item in temp_dir.iterdir() if item.is_dir()]
        if len(roots) != 1:
            raise RuntimeError(f"Unexpected zip layout in {zip_path}")
        dest = dest.expanduser().resolve()
        if dest.exists():
            if not dest.is_dir():
                raise RuntimeError(f"Custom node target exists and is not a directory: {dest}")
            if any(dest.iterdir()):
                raise RuntimeError(f"Custom node target is not empty; refusing to replace it: {dest}")
            dest.rmdir()
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(roots[0]), str(dest))


def install_node_from_zip(repo_url: str, dest: Path) -> None:
    with tempfile.TemporaryDirectory(prefix="comfy_node_download_") as temp_name:
        zip_path = Path(temp_name) / "node.zip"
        download_zip(github_zip_candidates(repo_url), zip_path)
        extract_single_root_zip(zip_path, dest)
    log(f"[ok] installed {dest.name} from zip")


def install_node_requirements(requirements: Path, comfy_python: Path | None) -> None:
    if comfy_python and comfy_python.exists():
        ensure_pip_available(comfy_python, requirements.parent)
        run_pip([str(comfy_python), "-m", "pip", "install", "-r", str(requirements)], requirements.parent)
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
            if git and (dest / ".git").exists():
                log(f"[update] {repo['name']}")
                run_optional([git, "pull", "--ff-only"], dest)
            else:
                log(f"[skip] {repo['name']}")
        else:
            if not git:
                log(f"[warn] git not found; downloading {repo['name']} source zip instead.")
                install_node_from_zip(str(repo["url"]), dest)
            else:
                try:
                    run([git, "clone", "--depth", "1", str(repo["url"]), str(dest)], custom_nodes_dir)
                except subprocess.CalledProcessError as exc:
                    log(f"[warn] git clone failed with {exc.returncode}; downloading {repo['name']} source zip instead.")
                    if dest.exists():
                        log(f"[cleanup] removing incomplete clone target: {dest}")
                        shutil.rmtree(dest)
                    install_node_from_zip(str(repo["url"]), dest)
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
    parser.add_argument("--dry-run", action="store_true", help="Print the install and disk-space plan without downloading.")
    parser.add_argument("--min-free-gb", type=float, default=10.0, help="Extra free disk space to keep after model downloads.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    base_dir = args.base_dir.resolve()
    log(f"[base] {base_dir}")
    log(f"[profile] {args.profile}")
    hf_endpoint = configured_hf_endpoint()
    if hf_endpoint:
        log(f"[download-source] Hugging Face endpoint: {hf_endpoint}")
    local_roots = local_asset_roots(base_dir)
    if local_roots:
        log("[local-assets] " + "; ".join(str(path) for path in local_roots))

    comfy_python = args.comfy_python.resolve() if args.comfy_python else None
    if comfy_python:
        log(f"[comfy-python] {comfy_python}")

    model_items = selected_model_items(args.profile, skip_t2v=args.skip_t2v, skip_loras=args.skip_loras)
    required_bytes = planned_download_bytes(base_dir, model_items)
    disk_plan = disk_space_plan(base_dir, required_bytes, gb_to_bytes(args.min_free_gb))
    log(f"[plan] model files in profile: {len(model_items)}")
    log_disk_plan(disk_plan)
    if args.dry_run:
        log("[dry-run] no files were downloaded or changed.")
        return 0
    if not disk_plan["ok"]:
        raise RuntimeError(
            "Not enough free disk space. "
            f"Need about {disk_plan['recommended_free_gb']} GB free, "
            f"but only {disk_plan['free_gb']} GB is available at {disk_plan['path']}."
        )

    if not args.no_custom_nodes and PROFILE_CUSTOM_NODES.get(args.profile, True):
        ensure_custom_nodes(
            base_dir,
            comfy_python=comfy_python,
            install_requirements=not args.no_node_requirements,
        )
    elif PROFILE_CUSTOM_NODES.get(args.profile, True) is False:
        log(f"[skip] custom nodes are not required for profile {args.profile}")

    for item in model_items:
        dest = base_dir / str(item["dest"])
        expected_bytes = int(item["bytes"])
        if install_from_local_asset(item, dest, expected_bytes, base_dir):
            continue
        download_file(effective_download_url(str(item["url"]), hf_endpoint), dest, expected_bytes)

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
