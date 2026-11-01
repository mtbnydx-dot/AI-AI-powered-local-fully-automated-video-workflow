"""Run the two Tide Letter tests, record diagnostics, and collect deliveries.

Services must already be running. This script never installs or downloads models.
Existing runs are resumed; rejected shots require an explicit videopipe retry.
"""

import argparse
import csv
import json
import os
import platform
import shutil
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from videopipe.delivery import collect_delivery
from videopipe.records import write_json


APP = Path(__file__).resolve().parents[1]
PACKAGE = APP / "examples/tide_letter"
CASES = {"smoke": ("package.smoke.json", "b200-smoke.yaml", 40),
         "full": ("package.json", "b200.yaml", 320)}


def capture(command):
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=30)
        return {"exit_code": result.returncode, "stdout": result.stdout, "stderr": result.stderr}
    except (OSError, subprocess.TimeoutExpired) as error:
        return {"error": str(error)}


def read_json(path, default=None):
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else default


def environment_snapshot(folder):
    folder.mkdir(parents=True, exist_ok=True)
    keys = ["COMFYUI_DIR", "COMFYUI_PYTHON", "VLLM_ENV", "QWEN_MODEL_PATH",
            "COMFYUI_REVISION", "VLLM_VERSION", "H3_REPO", "H3_REVISION", "QWEN_REPO",
            "QWEN_REVISION", "REVIEW_GPUS", "REVIEW_TP", "REVIEW_CONTEXT", "REVIEW_MAX_IMAGES",
            "REVIEW_MAX_SEQS", "REVIEW_GPU_MEMORY", "MODEL_MANIFEST", "FLASHINFER_WORKSPACE_BASE"]
    record = {"python": sys.version, "platform": platform.platform(),
              "configured": {key: os.environ.get(key) for key in keys},
              "gpu": capture(["nvidia-smi"]),
              "application_packages": capture([sys.executable, "-m", "pip", "freeze"])}
    for name, directory in (("comfyui", os.environ.get("COMFYUI_DIR")),
                            ("vllm", os.environ.get("VLLM_ENV"))):
        if directory:
            base = Path(directory)
            python = (os.environ.get("COMFYUI_PYTHON") or str(base / ".venv/bin/python")) if name == "comfyui" else str(base / "bin/python")
            record[name + "_packages"] = capture([python, "-m", "pip", "freeze"])
            if name == "comfyui":
                record["comfyui_commit"] = capture(["git", "-C", directory, "rev-parse", "HEAD"])
                record["comfyui_changes"] = capture(["git", "-C", directory, "diff", "--stat"])
    if os.environ.get("QWEN_MODEL_PATH"):
        record["qwen_config"] = read_json(Path(os.environ["QWEN_MODEL_PATH"]) / "config.json")
    manifest = Path(os.environ.get("MODEL_MANIFEST", "/nonexistent/model-manifest.json"))
    record["model_manifest_captured"] = manifest.is_file()
    if manifest.is_file():
        shutil.copy2(manifest, folder / "model-manifest.json")
    record["note"] = "Configured revisions are expectations; actual commits, package versions and model-manifest hashes are the evidence."
    write_json(folder / "environment.json", record)


def monitor(path, stop):
    with path.open("a", encoding="utf-8") as stream:
        while not stop.is_set():
            sample = {"time": datetime.now(timezone.utc).isoformat(), "gpus": []}
            gpu = capture(["nvidia-smi", "--query-gpu=index,uuid,memory.used,memory.total,utilization.gpu",
                           "--format=csv,noheader,nounits"])
            if gpu.get("exit_code") == 0:
                for row in csv.reader(gpu["stdout"].splitlines()):
                    if len(row) == 5:
                        sample["gpus"].append(dict(zip(
                            ("index", "uuid", "memory_used_mib", "memory_total_mib", "utilization_percent"),
                            [item.strip() for item in row])))
            else:
                sample["gpu_error"] = gpu
            try:
                memory = {line.split(":")[0]: int(line.split()[1])
                          for line in Path("/proc/meminfo").read_text().splitlines()
                          if line.startswith(("MemTotal:", "MemAvailable:"))}
                sample["host_memory_used_kib"] = memory["MemTotal"] - memory["MemAvailable"]
            except (OSError, KeyError, ValueError) as error:
                sample["host_memory_error"] = str(error)
            stream.write(json.dumps(sample) + "\n")
            stream.flush()
            stop.wait(5)


def run_case(name, root, session):
    manifest, config, _ = CASES[name]
    run = root / "runs" / name
    command = [sys.executable, "-m", "videopipe"]
    if (run / "state.json").is_file():
        command += ["resume", str(run), "--auto-approve"]
    else:
        command += ["run", str(PACKAGE / manifest), "--config", str(PACKAGE / "configs" / config),
                    "--output", str(run), "--auto-approve"]
    started = time.monotonic()
    with (session / (name + ".log")).open("w") as stream:
        result = subprocess.run(command, stdout=stream, stderr=subprocess.STDOUT, cwd=APP)
    write_json(session / (name + "-command.json"), {
        "command": command, "exit_code": result.returncode,
        "elapsed_seconds": round(time.monotonic() - started, 3)})
    print(f"{name}: exit={result.returncode}; log={session / (name + '.log')}", flush=True)


def collect(root):
    return collect_delivery(root, {name: root / "runs" / name for name in CASES})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path(os.environ.get("B200_TEST_OUTPUT", "runs/tide-b200")))
    parser.add_argument("--phase", choices=("all", "smoke", "full"), default="all")
    parser.add_argument("--collect-only", action="store_true", help="Rebuild deliveries/log archive after external retry")
    args = parser.parse_args()
    root = args.output.resolve()
    root.mkdir(parents=True, exist_ok=True)
    names = list(CASES) if args.phase == "all" else [args.phase]
    if not args.collect_only:
        session = root / "logs" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
        environment_snapshot(session)
        stop = threading.Event()
        sampler = threading.Thread(target=monitor, args=(session / "resources.jsonl", stop), daemon=True)
        sampler.start()
        try:
            for name in names:
                run_case(name, root, session)
        finally:
            stop.set()
            sampler.join(timeout=35)
    report = collect(root)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    required = list(CASES) if args.collect_only else names
    return 0 if all(report["cases"][name]["status"] == "complete" for name in required) else 2


if __name__ == "__main__":
    raise SystemExit(main())
