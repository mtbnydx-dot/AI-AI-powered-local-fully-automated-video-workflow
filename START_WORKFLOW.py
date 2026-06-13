from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path


WORKSPACE_DIR = Path(__file__).resolve().parent


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
FRONTEND_URL = os.environ.get("BEGINNER_FRONTEND_URL", "http://127.0.0.1:7860")
COMFY_URL = os.environ.get("COMFY_URL", "http://127.0.0.1:8000").rstrip("/")


def log(message: str) -> None:
    print(message, flush=True)


def get_json(url: str, timeout: float = 3.0) -> dict | None:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
        return None


def venv_python(venv_root: Path) -> Path:
    if platform.system() == "Windows":
        return venv_root / "Scripts" / "python.exe"
    return venv_root / "bin" / "python"


def python_version(python: Path) -> tuple[int, int, int] | None:
    try:
        result = subprocess.run(
            [
                str(python),
                "-c",
                "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}')",
            ],
            capture_output=True,
            check=True,
            text=True,
            timeout=10,
        )
    except Exception:
        return None
    try:
        major, minor, micro = result.stdout.strip().split(".")
        return int(major), int(minor), int(micro)
    except ValueError:
        return None


def python_has_module(python: Path, module: str) -> bool:
    result = subprocess.run(
        [str(python), "-c", f"import {module}"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    return result.returncode == 0


def python_supported(python: Path) -> bool:
    version = python_version(python)
    return bool(version and (3, 10, 0) <= version < (3, 13, 0))


def find_python() -> Path | None:
    candidates = [
        venv_python(WORKSPACE_DIR / ".venv"),
        venv_python(BASE_DIR / ".venv"),
        Path(sys.executable),
    ]
    seen: set[str] = set()
    for candidate in candidates:
        key = str(candidate)
        if key in seen:
            continue
        seen.add(key)
        if candidate.exists() and python_supported(candidate) and python_has_module(candidate, "uvicorn"):
            return candidate
    return None


def print_python_help() -> None:
    log("Python:    not ready")
    log("Need Python 3.10-3.12 with this project's requirements installed.")
    if platform.system() == "Windows":
        log("Run:")
        log(r"  py -3.12 -m venv .venv")
        log(r"  .\.venv\Scripts\python -m pip install -U pip")
        log(r"  .\.venv\Scripts\python -m pip install -r requirements.txt")
    else:
        log("Run:")
        log("  python3.12 -m venv .venv")
        log("  ./.venv/bin/python -m pip install -U pip")
        log("  ./.venv/bin/python -m pip install -r requirements.txt")


def wait_for_frontend(seconds: int = 20) -> bool:
    deadline = time.time() + seconds
    while time.time() < deadline:
        if get_json(f"{FRONTEND_URL}/api/environment", timeout=2):
            return True
        time.sleep(0.5)
    return False


def main() -> int:
    log("Wan2.2 local video workflow")
    log(f"Workspace: {WORKSPACE_DIR}")
    log(f"Base dir:  {BASE_DIR}")

    comfy_stats = get_json(f"{COMFY_URL}/system_stats", timeout=3)
    if comfy_stats:
        version = comfy_stats.get("system", {}).get("comfyui_version", "unknown")
        log(f"ComfyUI:   connected ({version})")
    else:
        log(f"ComfyUI:   not connected at {COMFY_URL}")
        log("           You can still open the workflow; the first step will show what is missing.")

    if get_json(f"{FRONTEND_URL}/api/environment", timeout=2):
        log(f"Frontend:  already running at {FRONTEND_URL}")
        webbrowser.open(FRONTEND_URL)
        return 0

    python = find_python()
    if python is None:
        print_python_help()
        return 1
    command = [
        str(python),
        "-m",
        "uvicorn",
        "beginner_frontend.app:app",
        "--host",
        "127.0.0.1",
        "--port",
        "7860",
    ]
    env = os.environ.copy()
    env["COMFY_URL"] = COMFY_URL
    env["COMFY_BASE_DIR"] = str(BASE_DIR)
    log(f"Python:    {python}")
    log(f"Frontend:  starting {FRONTEND_URL}")

    process = subprocess.Popen(command, cwd=str(WORKSPACE_DIR), env=env)
    if not wait_for_frontend():
        log("Frontend did not become ready. Check the terminal output above.")
        return process.poll() or 1

    webbrowser.open(FRONTEND_URL)
    log(f"Opened:    {FRONTEND_URL}")
    log("Keep this window open while using the workflow. Press Ctrl+C to stop the frontend.")
    try:
        return process.wait()
    except KeyboardInterrupt:
        log("Stopping frontend...")
        process.terminate()
        try:
            return process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            return process.wait()


if __name__ == "__main__":
    raise SystemExit(main())
