"""Export the current application source and CPU dependency wheels for offline installation."""

import argparse
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="Destination .tar.gz archive")
    args = parser.parse_args()
    source = Path(__file__).resolve().parents[1]
    if not (source / "requirements.lock").is_file():
        parser.error("requirements.lock is missing; generate the tested CPU dependency lock first")
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="videopipe-bundle-") as temporary:
        snapshot = Path(temporary) / "videopipe"
        _copy_source(source, snapshot)
        wheels = snapshot / "wheelhouse"
        wheels.mkdir()
        subprocess.run([
            sys.executable, "-m", "pip", "download", "--only-binary=:all:",
            "--dest", str(wheels), "--requirement", str(snapshot / "requirements.lock"),
        ], check=True)
        subprocess.run([
            sys.executable, "-m", "pip", "wheel", "--no-deps", "--wheel-dir", str(wheels), str(snapshot),
        ], check=True)
        shutil.rmtree(snapshot / "build", ignore_errors=True)
        shutil.rmtree(snapshot / "videopipe.egg-info", ignore_errors=True)
        with tarfile.open(output, "w:gz") as archive:
            archive.add(snapshot, arcname="videopipe")
    print(f"Offline application bundle: {output}")


def _copy_source(source: Path, target: Path) -> None:
    target.mkdir()
    for name in ("pyproject.toml", "requirements.txt", "requirements.lock", "README.md", "LICENSE"):
        path = source / name
        if path.is_file():
            shutil.copy2(path, target / name)
    for name in ("videopipe", "configs", "deploy", "docs", "examples", "scripts", "tests"):
        path = source / name
        if path.is_dir():
            shutil.copytree(path, target / name, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))


if __name__ == "__main__":
    main()
