from __future__ import annotations

import argparse
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path


COMFY_REPO = "https://github.com/Comfy-Org/ComfyUI.git"


def log(message: str) -> None:
    print(message, flush=True)


def run(command: list[str], cwd: Path | None = None) -> None:
    log(f"[run] {' '.join(command)}")
    subprocess.run(command, cwd=str(cwd) if cwd else None, check=True)


def venv_python(venv: Path) -> Path:
    if platform.system() == "Windows":
        return venv / "Scripts" / "python.exe"
    return venv / "bin" / "python"


def install_torch_command(python: Path, backend: str) -> list[str] | None:
    if backend == "skip":
        return None
    if backend == "auto":
        if platform.system() == "Darwin":
            backend = "mps"
        elif platform.system() == "Windows":
            backend = "cuda"
        else:
            backend = "cuda"
    if backend == "cuda":
        return [
            str(python),
            "-m",
            "pip",
            "install",
            "torch",
            "torchvision",
            "torchaudio",
            "--index-url",
            "https://download.pytorch.org/whl/cu130",
        ]
    if backend == "cpu":
        return [
            str(python),
            "-m",
            "pip",
            "install",
            "torch",
            "torchvision",
            "torchaudio",
            "--index-url",
            "https://download.pytorch.org/whl/cpu",
        ]
    if backend == "mps":
        return [str(python), "-m", "pip", "install", "torch", "torchvision", "torchaudio"]
    raise ValueError(f"Unknown backend: {backend}")


def ensure_repo(install_dir: Path) -> None:
    git = shutil.which("git")
    if not git:
        raise RuntimeError("git is required to install ComfyUI.")

    if (install_dir / ".git").exists():
        log(f"[update] {install_dir}")
        run([git, "pull", "--ff-only"], install_dir)
        return

    if install_dir.exists() and any(install_dir.iterdir()):
        raise RuntimeError(f"Install directory is not empty and is not a git repo: {install_dir}")

    install_dir.parent.mkdir(parents=True, exist_ok=True)
    log(f"[clone] {COMFY_REPO} -> {install_dir}")
    run([git, "clone", "--depth", "1", COMFY_REPO, str(install_dir)])


def ensure_venv(install_dir: Path) -> Path:
    venv = install_dir / ".venv"
    python = venv_python(venv)
    if not python.exists():
        log(f"[venv] creating {venv}")
        run([sys.executable, "-m", "venv", str(venv)])
    return python


def ensure_base_dirs(base_dir: Path) -> None:
    for name in ("models", "input", "output", "temp", "custom_nodes", "user"):
        (base_dir / name).mkdir(parents=True, exist_ok=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Install or update ComfyUI for the local video workflow.")
    parser.add_argument("--base-dir", type=Path, required=True)
    parser.add_argument("--install-dir", type=Path, required=True)
    parser.add_argument(
        "--backend",
        choices=["auto", "cuda", "cpu", "mps", "skip"],
        default="auto",
        help="PyTorch backend to install. Use skip if your ComfyUI venv already has torch.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    base_dir = args.base_dir.expanduser().resolve()
    install_dir = args.install_dir.expanduser().resolve()

    log(f"[base] {base_dir}")
    log(f"[install] {install_dir}")
    log(f"[backend] {args.backend}")

    ensure_base_dirs(base_dir)
    ensure_repo(install_dir)
    python = ensure_venv(install_dir)

    run([str(python), "-m", "pip", "install", "-U", "pip"])
    torch_command = install_torch_command(python, args.backend)
    if torch_command:
        run(torch_command)
    else:
        log("[torch] skipped")

    requirements = install_dir / "requirements.txt"
    if not requirements.exists():
        raise RuntimeError(f"ComfyUI requirements.txt not found: {requirements}")
    run([str(python), "-m", "pip", "install", "-r", str(requirements)], install_dir)

    log("ComfyUI install/update complete.")
    log("Next: start ComfyUI from the frontend, then install workflow assets.")
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
