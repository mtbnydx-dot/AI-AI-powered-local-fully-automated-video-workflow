"""Collect finished episodes without copying their working directories."""

import json
from pathlib import Path
import shutil
import tarfile

from .backends.asr import normalize_transcript
from .models import ScriptPackage
from .records import write_json
from .subtitles import create_subtitles


def collect_delivery(root: Path, runs: dict[str, Path] | None = None, *,
                     output: Path | None = None, errors: dict[str, str] | None = None) -> dict:
    root = root.resolve()
    delivery = (output or root / "delivery").resolve()
    delivery.mkdir(parents=True, exist_ok=True)
    if runs is None:
        index = root / "state.json"
        previous = json.loads(index.read_text()).get("cases", {}) if index.exists() else {}
        runs = {name: root / "runs" / name for name in previous}
        runs.update({path.name: path for path in sorted((root / "runs").glob("*")) if path.is_dir()})
        errors = {name: row["error"] for name, row in previous.items()
                  if row.get("error") and not (runs[name] / "state.json").exists()}
    cases, files = {}, []
    for name, run in runs.items():
        state_path = run / "state.json"
        state = json.loads(state_path.read_text()) if state_path.exists() else {"status": "not_run"}
        row = {"status": state["status"], "error": (errors or {}).get(name) or state.get("error")}
        if row["error"]:
            row["status"] = "failed"
        try:
            if row["status"] == "complete":
                final = run / state["final"]
                subtitles = _restore_subtitles(run, state)
                report_path = final.parent / "production_report.json"
                report = json.loads(report_path.read_text()) if report_path.exists() else dict(state)
                report.update(audio=state["audio"])
                write_json(report_path, report)
                write_json(delivery / name / "production_report.json",
                           {**report, "video": "final.mp4", "subtitles": "episode.srt"})
                files.append(delivery / name / "production_report.json")
                for source, filename in ((final, "final.mp4"), (subtitles, "episode.srt")):
                    target = delivery / name / filename
                    shutil.copy2(source, target)
                    files.append(target)
                row.update(video=f"{name}/final.mp4", subtitles=f"{name}/episode.srt",
                           report=f"{name}/production_report.json", media=state.get("delivery", {}),
                           subtitle_timing=state["audio"]["subtitles"]["timing"],
                           asr_status=state["audio"].get("transcript", {}).get("status", "unavailable"))
        except (OSError, ValueError, RuntimeError) as error:
            row.update(status="failed", error=str(error))
        if row["status"] != "complete":
            for filename in ("final.mp4", "episode.srt", "production_report.json"):
                target = delivery / name / filename
                target.unlink(missing_ok=True)
                if target in files:
                    files.remove(target)
        cases[name] = row
        events = run / "events.jsonl"
        if events.exists():
            target = delivery / "logs" / f"{name}.jsonl"
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(events, target)
            files.append(target)

    files.extend(_collect_logs(root, delivery))
    report = {"status": "complete" if cases and all(row["status"] == "complete" for row in cases.values()) else "incomplete",
              "cases": cases, "directory": str(delivery), "archive": str(delivery.with_name(delivery.name + ".tar.gz"))}
    write_json(delivery / "summary.json", report)
    lines = ["# Video delivery", "", f"Status: {report['status']}", ""]
    for name, row in cases.items():
        lines.append(f"- {name}: {row['status']}; subtitles={row.get('subtitle_timing', 'unavailable')}"
                     + (f"; error={row['error']}" if row.get("error") else ""))
    (delivery / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    files.extend([delivery / "summary.json", delivery / "summary.md"])
    # Archive only collected files, never an old nested archive or a working tree.
    with tarfile.open(report["archive"], "w:gz", compresslevel=1) as archive:
        for path in files:
            archive.add(path, arcname=Path("delivery") / path.relative_to(delivery))
    write_json(root / "state.json", report)
    return report


def _restore_subtitles(run: Path, state: dict) -> Path:
    """Rebuild subtitles from saved ASR/script data, including pre-fallback runs."""
    package = ScriptPackage.model_validate_json((run / "package.json").read_text())
    shots = [shot.model_copy(update={"duration": state["shots"][shot.id]["duration"]}) for shot in package.shots]
    folder = (run / state["draft"]).parent / "audio"
    audio = state.setdefault("audio", {})
    transcript = audio.get("transcript", {"status": "unavailable", "segments": []})
    worker = folder / "worker-result.json"
    if worker.exists():
        try:
            transcript = normalize_transcript(json.loads(worker.read_text()))
        except ValueError as error:
            transcript = {"status": "unavailable", "segments": [], "error": str(error)}
    audio["transcript"] = transcript
    audio["subtitles"] = create_subtitles(shots, transcript, folder / "subtitles")
    write_json(folder / "transcript.json", transcript)
    write_json(run / "state.json", state)
    source = Path(audio["subtitles"]["path"])
    shutil.copy2(source, (run / state["final"]).parent / "episode.srt")
    return source


def _collect_logs(root: Path, delivery: Path) -> list[Path]:
    sources = []
    if (root / "events.jsonl").exists():
        sources.append((root / "events.jsonl", "batch.jsonl"))
    for path in sorted((root / "logs").rglob("*")):
        if path.is_file() and (path.suffix == ".log" or path.name == "resources.jsonl"):
            sources.append((path, "runner-" + "-".join(path.relative_to(root / "logs").parts)))
    for path in sorted((root / "services").glob("*.log")):
        sources.append((path, "service-" + path.name))
    files = []
    for source, name in sources:
        target = delivery / "logs" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        files.append(target)
    return files
