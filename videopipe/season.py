"""Sequential episode orchestration over the existing single-run pipeline."""

import json
from pathlib import Path
import sys

from pydantic import BaseModel, Field

from .delivery import collect_delivery
from .inputs import load_config, load_package
from .pipeline import Production
from .records import Events


class Episode(BaseModel):
    id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
    package: Path
    config: Path | None = None


class Season(BaseModel):
    episodes: list[Episode] = Field(min_length=1)


def run_season(source: Path, output: Path, *, config: Path | None = None,
               auto_approve: bool = False) -> dict:
    source, output = source.resolve(), output.resolve()
    season = Season.model_validate_json((source / "overview.json").read_text())
    names = [episode.id for episode in season.episodes]
    if len(set(names)) != len(names):
        raise ValueError("Episode IDs must be unique")
    runs = {name: output / "runs" / name for name in names}
    output.mkdir(parents=True, exist_ok=True)
    events, errors = Events(output), {}
    try:
        for episode in season.episodes:
            run = runs[episode.id]
            print(f"{episode.id}: running/resuming", file=sys.stderr, flush=True)
            events.emit("episode_started", episode=episode.id)
            try:
                if (run / "state.json").exists():
                    state = json.loads((run / "state.json").read_text())
                    if state["status"] != "complete":
                        state = Production(run).run(auto_approve=auto_approve)
                else:
                    package = source / episode.package
                    settings = config or (source / episode.config if episode.config else package.parent / "production.yaml")
                    state = Production.create(load_package(package), load_config(settings), run).run(auto_approve=auto_approve)
                events.emit("episode_finished", episode=episode.id, status=state["status"])
                print(f"{episode.id}: {state['status']}", file=sys.stderr, flush=True)
            except Exception as error:
                errors[episode.id] = str(error)
                events.emit("episode_failed", episode=episode.id, error=str(error))
                print(f"{episode.id}: failed: {error}", file=sys.stderr, flush=True)
    finally:
        events.close()
        report = collect_delivery(output, runs, errors=errors)
    return report
