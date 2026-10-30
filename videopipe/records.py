import json
import logging
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


class Events:
    def __init__(self, run_dir: Path):
        self.logger = logging.Logger(str(run_dir))
        handler = logging.FileHandler(run_dir / "events.jsonl", encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(message)s"))
        self.logger.addHandler(handler)
        self.logger.setLevel(logging.INFO)

    def emit(self, event: str, **values) -> None:
        self.logger.info(json.dumps({"time": now(), "event": event, **values}, ensure_ascii=False))

    @contextmanager
    def stage(self, stage: str, **values):
        started = time.monotonic()
        self.emit("stage_started", stage=stage, **values)
        try:
            yield
        except BaseException as error:
            self.emit("stage_finished", stage=stage, status="failed",
                      elapsed=round(time.monotonic() - started, 3), error=str(error), **values)
            raise
        else:
            self.emit("stage_finished", stage=stage, status="complete",
                      elapsed=round(time.monotonic() - started, 3), **values)

    def close(self) -> None:
        for handler in self.logger.handlers:
            handler.close()
