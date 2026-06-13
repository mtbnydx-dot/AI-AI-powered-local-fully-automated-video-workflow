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
BASE_DIR = Path(os.environ.get("COMFY_BASE_DIR", WORKSPACE_DIR.parent)).expanduser().resolve()
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


def find_python() -> Path:
    if platform.system() == "Windows":
        candidate = BASE_DIR / ".venv" / "Scripts" / "python.exe"
    else:
        candidate = BASE_DIR / ".venv" / "bin" / "python"
    if candidate.exists():
        return candidate
    return Path(sys.executable)


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
