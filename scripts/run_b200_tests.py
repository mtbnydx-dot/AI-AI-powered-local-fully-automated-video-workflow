"""Run the two Tide Letter tests, record diagnostics, and collect verified deliveries.

Services must already be running. This script never installs or downloads models.
Existing runs are resumed; rejected shots require an explicit videopipe retry.
"""

import argparse
import csv
import hashlib
import json
import math
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from videopipe.media import inspect_video, run_ffmpeg
from videopipe.records import write_json


APP = Path(__file__).resolve().parents[1]
PACKAGE = APP / "examples/tide_letter"
CASES = {"smoke": ("package.smoke.json", "b200-smoke.yaml", 40),
         "full": ("package.json", "b200.yaml", 320)}
VIDEO_SUFFIXES = {".mp4", ".webm", ".mkv", ".mov", ".avi"}


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
    delivery = root / "delivery"
    delivery.mkdir(exist_ok=True)
    cases = {}
    for name, (_, _, duration) in CASES.items():
        run = root / "runs" / name
        target = delivery / f"tide-{duration}s.mp4"
        state = read_json(run / "state.json", {})
        shots = state.get("shots", {})
        row = {"status": state.get("status", "not_run"), "run_dir": str(run),
               "planned_seconds": duration, "expected_seconds": duration, "error": state.get("error"),
               "retries": sum(max(0, s.get("attempts", 0) - 1) for s in shots.values()),
               "visual_pass_count": sum(s.get("selection", {}).get("reason") == "review_pass" for s in shots.values()),
               "fallback_shots": [shot_id for shot_id, shot in shots.items()
                                  if shot.get("selection", {}).get("reason") in ("review_unavailable", "budget_exhausted")],
               "stage_seconds_sum": {}, "verified": False}
        if state.get("manifest"):
            row["expected_seconds"] = None
            try:
                manifest_path = (run / state["manifest"]).resolve()
                if not manifest_path.is_relative_to(run.resolve()):
                    raise ValueError("Assembly manifest is outside its run directory")
                manifest = read_json(manifest_path)
                if not isinstance(manifest, list) or not manifest:
                    raise ValueError("Assembly manifest must contain measured clip durations")
                durations = [clip.get("duration") if isinstance(clip, dict) else None for clip in manifest]
                if any(isinstance(value, bool) or not isinstance(value, (int, float))
                       or not math.isfinite(value) or value <= 0 for value in durations):
                    raise ValueError("Assembly manifest contains an invalid clip duration")
                row["expected_seconds"] = math.fsum(durations)
            except (OSError, ValueError) as error:
                row["verification_error"] = str(error)
        events = run / "events.jsonl"
        if events.is_file():
            for line in events.read_text().splitlines():
                event = json.loads(line)
                if event["event"] == "stage_finished":
                    stage = event["stage"]
                    row["stage_seconds_sum"][stage] = round(row["stage_seconds_sum"].get(stage, 0) + event["elapsed"], 3)
        if row["status"] == "complete" and state.get("final") and not row.get("verification_error"):
            try:
                video = (run / state["final"]).resolve()
                if not video.is_relative_to(run.resolve()):
                    raise ValueError("Final video is outside its run directory")
                info = inspect_video(video)
                media = read_json(run / "config.json", {}).get("media", {})
                factor = media.get("upscale_factor", 2) if media.get("upscale", "none") != "none" else 1
                expected_size = (media.get("width", 576) * factor, media.get("height", 1024) * factor)
                if (info.width, info.height) != expected_size or abs(info.fps - media.get("fps", 24)) > 0.01 or abs(info.duration - row["expected_seconds"]) > 0.15:
                    raise ValueError(f"Unexpected delivery metadata: {info}")
                run_ffmpeg(["-v", "error", "-xerror", "-i", str(video), "-f", "null", "-"])
                shutil.copy2(video, target)
                with target.open("rb") as stream:
                    checksum = hashlib.file_digest(stream, "sha256").hexdigest()
                row.update(verified=True, video=str(target), sha256=checksum, media=vars(info))
            except (OSError, ValueError, RuntimeError) as error:
                row["verification_error"] = str(error)
        if not row["verified"]:
            # Remove only our exported copy; original versioned deliveries remain in the run.
            target.unlink(missing_ok=True)
        cases[name] = row
    peaks = {}
    host_peak = 0
    for path in (root / "logs").glob("*/resources.jsonl"):
        for line in path.read_text().splitlines():
            sample = json.loads(line)
            host_peak = max(host_peak, sample.get("host_memory_used_kib", 0))
            for gpu in sample["gpus"]:
                try:
                    peaks[gpu["index"]] = max(peaks.get(gpu["index"], 0), float(gpu["memory_used_mib"]))
                except ValueError:
                    pass
    commands = []
    for path in sorted((root / "logs").glob("*/*-command.json")):
        commands.append({"case": path.name.split("-")[0], "record": str(path.relative_to(root)), **read_json(path)})
    report = {"status": "complete" if all(c["verified"] for c in cases.values()) else "incomplete",
              "cases": cases, "commands": commands, "sampled_gpu_peak_mib": peaks,
              "sampled_host_memory_peak_kib": host_peak,
              "notes": ["Resource samples cover the whole node at approximately 5-second intervals; peaks may be missed.",
                        "Stage totals sum concurrent work and are not wall-clock duration.",
                        "Verified means technical media validation, not visual review approval; fallback shots may still be delivered.",
                        "Audio presence is recorded; sparse visual Review does not assess audio quality or prove smooth motion."]}
    write_json(delivery / "summary.json", report)
    lines = ["# B200 两轮测试结果", "", f"状态：{report['status']}", ""]
    for name, row in cases.items():
        lines.append(f"- {name} / 计划{row['planned_seconds']}秒，验收时长{row['expected_seconds']}秒：{row['status']}，技术媒体验证={row['verified']}，返工次数={row['retries']}")
        lines.append(f"  画面审核通过镜头数：{row['visual_pass_count']}；降级选片镜头：{', '.join(row['fallback_shots']) or '无'}")
        elapsed = sum(command["elapsed_seconds"] for command in commands if command["case"] == name)
        lines.append(f"  测试入口命令累计耗时：{elapsed:.1f}秒；阶段累计用时：{json.dumps(row['stage_seconds_sum'], ensure_ascii=False)}")
        if row.get("verification_error") or row.get("error"):
            lines.append(f"  错误：{row.get('verification_error') or row.get('error')}")
    lines += ["", f"采样GPU显存峰值（MiB）：{json.dumps(peaks)}；主机内存峰值（KiB）：{host_peak}。",
              "命令耗时不含入口外执行的手动retry；阶段耗时包含其工作，详细原始记录见日志。",
              "技术媒体验证通过不等于画面审核通过；降级选片仍可进入交付，需结合 selection 与最终人工验收。",
              "详细耗时、资源采样与文件校验值见 summary.json；音频内容、口型和流畅性留待最终人工验收。"]
    (delivery / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    with tarfile.open(delivery / "logs.tar.gz", "w:gz") as archive:
        for folder in (root / "runs", root / "logs", root / "services"):
            if folder.exists():
                for path in sorted(folder.rglob("*")):
                    if path.is_file() and not path.is_symlink() and path.suffix.lower() not in VIDEO_SUFFIXES:
                        archive.add(path, arcname=path.relative_to(root))
        for name in ("summary.json", "summary.md"):
            archive.add(delivery / name, arcname=name)
    return report


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
    return 0 if all(report["cases"][name]["verified"] for name in required) else 2


if __name__ == "__main__":
    raise SystemExit(main())
