import json
import shutil
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from queue import Queue

from .inputs import load_config, load_package, validate_plan
from .models import PipelineConfig, ScriptPackage, Shot
from .records import Events, now, write_json


class Production:
    def __init__(self, run_dir: Path):
        self.root = run_dir.resolve()
        self.package = load_package(self.root / "package.json")
        self.config = load_config(self.root / "config.json")
        self.state = json.loads((self.root / "state.json").read_text(encoding="utf-8"))
        self.events = Events(self.root)
        self.generators, self.reviewer = self._backends()

    @classmethod
    def create(cls, package: ScriptPackage, config: PipelineConfig, run_dir: Path):
        validate_plan(package, config)
        root = run_dir.resolve()
        root.mkdir(parents=True, exist_ok=False)
        _snapshot(root, package, config)
        write_json(root / "state.json", {
            "version": 1, "title": package.title, "created_at": now(),
            "status": "pending", "film_reviews": 0, "film_cycle": 0,
            "shots": {shot.id: {"status": "pending", "attempts": 0, "budget_used": 0}
                      for shot in package.shots},
        })
        return cls(root)

    def run(self, *, auto_approve: bool = False) -> dict:
        try:
            return self._run(auto_approve=auto_approve)
        except (Exception, KeyboardInterrupt) as exc:
            self.state.update(status="failed", error=str(exc) or "Interrupted")
            self.events.emit("run_failed", error=self.state["error"])
            self._save()
            raise
        finally:
            self.events.close()

    def _run(self, *, auto_approve: bool) -> dict:
        from .media import assemble

        if self.state["status"] == "complete":
            return self.state
        if self.state.get("approval"):
            return self._deliver()
        if self.state["status"] == "awaiting_approval":
            if auto_approve:
                self.state["approval"] = {"time": now(), "method": "automatic"}
                return self._deliver()
            return self.state
        self.state.update(status="running", error=None)
        self.events.emit("run_started", generator=self.config.generator,
                         reviewer=self.config.review.backend)
        self._save()
        while self.state.get("film_pending") or self.state["film_cycle"] < self.config.max_film_reviews:
            if not self._generate_pending():
                self.state["status"] = "needs_attention"
                self._save()
                return self.state
            if not self.state.get("film_pending"):
                self.state["film_reviews"] += 1
                folder = self.root / "film" / f"review-{self.state['film_reviews']:03d}"
                folder.mkdir(parents=True, exist_ok=True)
                with self.events.stage("assemble", review=self.state["film_reviews"]):
                    draft = assemble([self.root / self.state["shots"][s.id]["clip"]
                                      for s in self.package.shots], folder / "draft.mp4", self.config.media)
                self.state.update(draft=str(draft.relative_to(self.root)), film_pending=True)
                self._save()
            index = self.state["film_reviews"]
            draft = self.root / self.state["draft"]
            folder = draft.parent
            reviewed_shots = [shot.model_copy(update={"prompt": self.state["shots"][shot.id].get("prompt", shot.prompt)})
                              for shot in self.package.shots]
            with self.events.stage("film_review", review=index):
                result = self.reviewer.review(draft, shots=reviewed_shots, scope="film", output_dir=folder)
            self.state["film_cycle"] += 1
            self.state["film_pending"] = False
            write_json(folder / "result.json", result.model_dump(mode="json"))
            self.events.emit("film_reviewed", review=index, passed=result.passed, summary=result.summary)
            self.state["film_result"] = str((folder / "result.json").relative_to(self.root))
            if result.passed:
                if auto_approve or not self.config.human_approval:
                    self.state["approval"] = {"time": now(), "method": "automatic"}
                    return self._deliver()
                self.state["status"] = "awaiting_approval"
                self._save()
                return self.state
            if not self._apply_film_feedback(result):
                self.state["status"] = "needs_attention"
                self._save()
                return self.state
            self._save()
        self.state.update(status="needs_attention", error="Full-film review budget exhausted")
        self._save()
        return self.state

    def approve(self) -> dict:
        if self.state["status"] != "awaiting_approval":
            self.events.close()
            raise ValueError("Run must be awaiting_approval before approval")
        self.state["approval"] = {"time": now(), "method": "cli"}
        self._save()
        return self.run()

    def retry(self, shot_ids: list[str], *, prompt: str | None = None, feedback: str = "") -> None:
        unknown = set(shot_ids) - self.state["shots"].keys()
        if unknown:
            raise ValueError(f"Unknown shot IDs: {', '.join(sorted(unknown))}")
        for shot_id in shot_ids:
            record = self.state["shots"][shot_id]
            record.update(status="pending", budget_used=0, feedback=feedback, stage="generate")
            if prompt is not None:
                record["prompt"] = prompt
        for key in ("approval", "final", "delivery", "error"):
            self.state.pop(key, None)
        self.state.update(status="pending", film_cycle=0, film_pending=False)
        self.events.emit("retry_requested", shots=shot_ids, prompt=prompt, feedback=feedback)
        self._save()

    def _generate_pending(self) -> bool:
        pending = [shot for shot in self.package.shots if self.state["shots"][shot.id]["status"] != "passed"]
        with ThreadPoolExecutor(max_workers=self.config.workers) as pool:
            futures = {pool.submit(self._produce_shot, shot, dict(self.state["shots"][shot.id])): shot
                       for shot in pending}
            for future in as_completed(futures):
                shot = futures[future]
                self.state["shots"][shot.id] = future.result()
                self._save()
        return all(record["status"] == "passed" for record in self.state["shots"].values())

    def _produce_shot(self, shot: Shot, record: dict) -> dict:
        from .media import normalize_clip

        while record.get("stage") in {"normalize", "review"} or record["budget_used"] < self.config.max_attempts:
            continuing = record.get("stage") in {"normalize", "review"}
            if not continuing:
                record["attempts"] += 1
                record["budget_used"] += 1
                record["stage"] = "generate"
            attempt = record["attempts"]
            folder = self.root / "shots" / shot.id / f"attempt-{attempt:03d}"
            folder.mkdir(parents=True, exist_ok=True)
            current = shot.model_copy(update={"prompt": record.get("prompt", shot.prompt)})
            started = time.monotonic()
            self.events.emit("shot_started", shot=shot.id, attempt=attempt)
            write_json(folder / "shot.json", current.model_dump(mode="json"))
            try:
                if record["stage"] == "generate":
                    generator = self.generators.get()
                    try:
                        with self.events.stage("generate", shot=shot.id, attempt=attempt):
                            source = generator.generate(current, folder, attempt=attempt, feedback=record.get("feedback", ""))
                    finally:
                        self.generators.put(generator)
                    record.update(source=str(source.relative_to(self.root)), stage="normalize")
                if record["stage"] == "normalize":
                    with self.events.stage("normalize", shot=shot.id, attempt=attempt):
                        clip = normalize_clip(self.root / record["source"], folder / "clip.mp4",
                                              duration=shot.duration, config=self.config.media)
                    record.update(clip=str(clip.relative_to(self.root)), stage="review")
                clip = self.root / record["clip"]
                review_dir = folder / "review"
                review_dir.mkdir(exist_ok=True)
                with self.events.stage("shot_review", shot=shot.id, attempt=attempt):
                    result = self.reviewer.review(clip, shots=[current], scope="shot", output_dir=review_dir)
                write_json(review_dir / "result.json", result.model_dump(mode="json"))
                record.update(clip=str(clip.relative_to(self.root)),
                              review=str((review_dir / "result.json").relative_to(self.root)))
                self.events.emit("shot_reviewed", shot=shot.id, attempt=attempt, passed=result.passed,
                                 elapsed=round(time.monotonic() - started, 3), summary=result.summary)
                if result.passed:
                    record.update(status="passed", error=None, stage="complete")
                    return record
                record["feedback"] = result.summary + "\n" + "\n".join(i.message for i in result.issues)
                for issue in result.issues:
                    if issue.suggested_prompt:
                        record["prompt"] = issue.suggested_prompt
                record.update(status="rejected", error=result.summary, stage="generate")
            except Exception as exc:
                record.update(status="failed", error=str(exc))
                self.events.emit("shot_failed", shot=shot.id, attempt=attempt,
                                 elapsed=round(time.monotonic() - started, 3), error=str(exc))
                write_json(folder / "error.json", {"error": str(exc)})
                return record
        record["status"] = "exhausted"
        return record

    def _apply_film_feedback(self, result) -> bool:
        if not result.issues:
            self.state["error"] = "Rejected film has no actionable issues"
            return False
        editing = next((issue for issue in result.issues if issue.category == "editing"), None)
        if editing:
            self.state["error"] = "Editing feedback requires an updated input: " + editing.message
            return False
        for issue in result.issues:
            targets = [issue.shot_id] if issue.shot_id else [s.id for s in self.package.shots]
            for shot_id in targets:
                record = self.state["shots"][shot_id]
                record.update(status="pending", feedback=issue.message, stage="generate")
                if issue.suggested_prompt:
                    record["prompt"] = issue.suggested_prompt
        return True

    def _deliver(self) -> dict:
        from .media import finalize, inspect_video

        self.state["status"] = "delivering"
        self._save()
        source = self.root / self.state["draft"]
        config = self.config.media
        folder = self.root / "delivery" / f"version-{self.state['film_reviews']:03d}"
        folder.mkdir(parents=True, exist_ok=True)
        if config.upscale == "comfyui":
            from .backends.comfyui import ComfyGenerator
            source = ComfyGenerator(self.config.comfyui).upscale(source, folder / "upscale", factor=config.upscale_factor)
            config = config.model_copy(update={"upscale": "none", "width": config.width * config.upscale_factor,
                                               "height": config.height * config.upscale_factor})
        with self.events.stage("finalize", review=self.state["film_reviews"]):
            final = finalize(source, folder / "final.mp4", config)
        info = inspect_video(final)
        self.state.update(status="complete", final=str(final.relative_to(self.root)),
                          completed_at=now(), delivery={"width": info.width, "height": info.height,
                          "duration": info.duration, "fps": info.fps, "has_audio": info.has_audio})
        self.events.emit("run_completed", final=self.state["final"], **self.state["delivery"])
        self._save()
        return self.state

    def _backends(self):
        from .backends.cpu import CPUGenerator
        from .backends.review import CPUReviewer, VLLMReviewer

        generators = Queue()
        if self.config.generator == "cpu":
            for _ in range(self.config.workers):
                generators.put(CPUGenerator())
        else:
            from .backends.comfyui import ComfyGenerator
            for url in self.config.comfyui.urls:
                generators.put(ComfyGenerator(self.config.comfyui, url=url))
        reviewer_type = CPUReviewer if self.config.review.backend == "cpu" else VLLMReviewer
        reviewer = reviewer_type(self.config.review, script=self.package.script.read_text(encoding="utf-8"))
        return generators, reviewer

    def _save(self) -> None:
        self.state["updated_at"] = now()
        write_json(self.root / "state.json", self.state)


def _snapshot(root: Path, package: ScriptPackage, config: PipelineConfig) -> None:
    package = package.model_copy(deep=True)
    config = config.model_copy(deep=True)
    inputs = root / "inputs"
    inputs.mkdir()
    shutil.copy2(package.script, inputs / "script.md")
    package.script = Path("inputs/script.md")
    for shot in package.shots:
        refs = []
        for index, ref in enumerate(shot.reference_images):
            dest = Path("inputs") / f"{shot.id}-{index}{ref.suffix}"
            shutil.copy2(ref, root / dest)
            refs.append(dest)
        shot.reference_images = refs
    for name in ("workflow", "upscale_workflow"):
        source = getattr(config.comfyui, name)
        if source is not None:
            dest = Path("inputs") / f"{name}.json"
            shutil.copy2(source, root / dest)
            setattr(config.comfyui, name, dest)
    write_json(root / "package.json", package.model_dump(mode="json"))
    write_json(root / "config.json", config.model_dump(mode="json"))
