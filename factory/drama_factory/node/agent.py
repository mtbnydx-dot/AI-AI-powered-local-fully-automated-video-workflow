"""Node agent: one worker per inference instance pulls jobs for that instance's pools over HTTP.

It never runs anything a package supplies. The only command it executes is the node's own
`switch_command` from local config, after draining, when the control plane asks for another GPU profile.
"""
import json
import logging
import os
import platform
import shlex
import subprocess
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import httpx

from ..cluster import profiles
from ..cluster.config import NodeConfig
from ..policy.review import LEVEL_POOLS
from ..storage import make_store
from . import backends, probe, telemetry

log = logging.getLogger(__name__)
POOL_LEVELS = {pool: level for level, pool in LEVEL_POOLS.items()}


@dataclass
class Lease:
    job_id: str
    token: str
    cancelled: bool = False
    confirm: Callable[[], dict] | None = None  # one synchronous lease heartbeat

    def alive(self) -> bool:
        """Re-check before a side effect such as uploading. An unreachable control plane counts as
        alive: outputs go to per-attempt keys, so a stale upload cannot overwrite anything."""
        if self.cancelled or self.confirm is None:
            return not self.cancelled
        try:
            reply = self.confirm()
        except httpx.HTTPError:
            return True
        if reply.get("cancelled") or reply.get("ok") is False:
            self.cancelled = True
        return not self.cancelled


Handler = Callable[[dict, Lease], dict]


class ControlClient:
    def __init__(self, base_url: str, token: str, client: httpx.Client | None = None, retries: int = 3):
        self.client = client or httpx.Client(base_url=base_url, timeout=60)
        self.headers = {"Authorization": f"Bearer {token}"}
        self.retries = retries

    def _post(self, path: str, body: dict, idempotent: bool = True) -> dict:
        # A non-idempotent call (claim) is retried only if the request surely never reached the server;
        # retrying after a read timeout could lease a second job and orphan the first one.
        retry_on = httpx.TransportError if idempotent else (httpx.ConnectError, httpx.ConnectTimeout)
        for attempt in range(self.retries):
            try:
                response = self.client.post(path, json=body, headers=self.headers)
                if response.status_code == 409:  # lease lost or result already in: nothing to retry
                    return {"conflict": True, "detail": response.json().get("detail")}
                response.raise_for_status()
                return response.json()
            except retry_on:
                if attempt == self.retries - 1:
                    raise
                time.sleep(2 ** attempt)
        return {}

    def heartbeat(self, node_id: str, info: dict) -> dict:
        return self._post(f"/v1/nodes/{node_id}/heartbeat", info)

    def claim(self, node_id: str, worker: str, pools: list[str], limit: int = 1) -> list[dict]:
        return self._post("/v1/jobs/claim", {"node_id": node_id, "worker": worker, "pools": pools, "limit": limit},
                          idempotent=False).get("jobs", [])

    def job_heartbeat(self, job_id: str, token: str) -> dict:
        return self._post(f"/v1/jobs/{job_id}/heartbeat", {"lease_token": token})

    def complete(self, job_id: str, token: str, result: dict) -> dict:
        return self._post(f"/v1/jobs/{job_id}/complete", {"lease_token": token, "result": result})

    def fail(self, job_id: str, token: str, code: str, message: str, retryable: bool) -> dict:
        return self._post(f"/v1/jobs/{job_id}/fail", {"lease_token": token, "code": code, "message": message,
                                                      "retryable": retryable})


class LeaseKeeper:
    """Extends the lease while a long job runs and flags cancellation (take superseded or lease lost)."""

    def __init__(self, client: ControlClient, lease: Lease, interval: float | None):
        self.client, self.lease, self.interval = client, lease, interval
        self._stop = threading.Event()
        self._thread = None

    def __enter__(self):
        if self.interval:
            self._thread = threading.Thread(target=self._run, name=f"lease-{self.lease.job_id[:8]}", daemon=True)
            self._thread.start()
        return self

    def __exit__(self, *_exc):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)

    def _run(self) -> None:
        while not self._stop.wait(self.interval):
            try:
                reply = self.client.job_heartbeat(self.lease.job_id, self.lease.token)
                if reply.get("cancelled") or reply.get("ok") is False:
                    self.lease.cancelled = True
            except httpx.HTTPError as exc:
                log.warning("lease heartbeat failed for %s: %s", self.lease.job_id, exc)


def generation_handler(backend, store, work_dir: Path, weight: float, probe_fn=probe.probe_video) -> Handler:
    def handle(job: dict, lease: Lease) -> dict:
        payload, cache = job["payload"], work_dir / "cache"
        inputs = {name: store.fetch(payload[name], cache) for name in ("first_frame", "last_frame") if payload.get(name)}
        inputs.update({ref["token"]: store.fetch(ref["key"], cache) for ref in payload.get("references", [])})
        out = work_dir / "out" / Path(payload["output_key"]).name
        out.parent.mkdir(parents=True, exist_ok=True)
        started = time.monotonic()
        info = backend.generate(payload, inputs, out, lease)
        elapsed = time.monotonic() - started
        if not lease.alive():
            out.unlink(missing_ok=True)
            raise backends.BackendError("CANCELLED", "租约已失效或 take 已被替代，放弃上传", retryable=False)
        metrics = probe_fn(out)
        store.put(out, payload["output_key"])
        out.unlink(missing_ok=True)
        return {"output_key": payload["output_key"], "probe": metrics, "gpu_seconds": round(elapsed * weight, 3),
                "elapsed_s": round(elapsed, 3), "model_version": info.get("model_version", ""),
                "seed": payload["seed"]}
    return handle


def review_handler(reviewer, store, work_dir: Path) -> Handler:
    def handle(job: dict, lease: Lease) -> dict:
        payload, cache = job["payload"], work_dir / "cache"
        video = store.fetch(payload["video_key"], cache)
        refs = [{**ref, "path": store.fetch(ref["key"], cache)} for ref in payload.get("references", [])]
        context = {"previous": store.fetch(payload["previous"]["video_key"], cache)} if payload.get("previous") else {}
        return reviewer.review(payload, video, refs, context)
    return handle


def director_handler(director) -> Handler:
    return lambda job, lease: director.plan(job["payload"])


class Worker:
    def __init__(self, agent: "NodeAgent", name: str, handlers: dict[str, Handler]):
        self.agent, self.name, self.handlers = agent, name, handlers

    def step(self) -> bool:
        jobs = self.agent.client.claim(self.agent.config.node_id, self.name, list(self.handlers))
        if not jobs:
            return False
        job, client = jobs[0], self.agent.client
        lease = Lease(job["job_id"], job["lease_token"],
                      confirm=lambda: client.job_heartbeat(job["job_id"], job["lease_token"]))
        self.agent.track(+1)
        try:
            with LeaseKeeper(client, lease, self.agent.lease_interval):
                result = self.handlers[job["pool"]](job, lease)
        except backends.BackendError as exc:
            client.fail(lease.job_id, lease.token, exc.code, str(exc), exc.retryable)
            return True
        except Exception as exc:  # engine bug or I/O problem: report, never crash the worker
            log.exception("job %s failed", lease.job_id)
            client.fail(lease.job_id, lease.token, "ENGINE_ERROR", repr(exc)[:2000], True)
            return True
        finally:
            self.agent.track(-1)
        client.complete(lease.job_id, lease.token, result)
        return True


class NodeAgent:
    def __init__(self, config: NodeConfig, client: ControlClient, store=None,
                 handlers: dict[str, Handler] | None = None, telemetry_fn: Callable[[], dict] | None = None,
                 lease_interval: float | None = 60.0, runner=subprocess.run, health_timeout_s: float = 3600):
        self.config, self.client = config, client
        self.work_dir = Path(config.work_dir)
        self.store = store or make_store(config.store, self.work_dir / "cache")
        self.telemetry_fn = telemetry_fn or (lambda: telemetry.collect(config.disk_paths))
        self.lease_interval, self.runner, self.health_timeout_s = lease_interval, runner, health_timeout_s
        self._override = handlers
        self._lock = threading.Lock()
        self.boot_id = uuid.uuid4().hex
        self.inflight, self.draining, self.switch_to = 0, False, None
        self._switch_failures, self._retry_at = 0, 0.0
        # What actually runs on the GPUs survives restarts in agent-state.json; node.yaml's profile is only
        # the first-boot default. A switch interrupted by a crash is finished before any job is claimed.
        self.state_path = self.work_dir / "agent-state.json"
        state = self._load_state()
        pending = state.get("switching_to")
        self.pending = pending if pending in config.profiles else None
        saved = state.get("profile")
        self.profile = self.pending or (saved if saved in config.profiles else config.profile)
        self.workers = self._build_workers()

    def track(self, delta: int) -> None:
        with self._lock:
            self.inflight += delta

    def _handler(self, pool: str, instance, model) -> Handler:
        if self._override is not None:
            return self._override[pool]
        base_url = f"http://127.0.0.1:{instance.port}"
        if pool.startswith("gen."):
            backend = backends.build_generator(model.backend, base_url)
            stub = isinstance(backend, backends.SimulatedGenerator) and not backend.render
            return generation_handler(backend, self.store, self.work_dir, profiles.gpu_weight(self.config, instance),
                                      backends.stub_probe if stub else probe.probe_video)
        if pool == "director":
            return director_handler(backends.build_director(model.backend, base_url))
        return review_handler(backends.build_reviewer(model.backend, base_url, POOL_LEVELS[pool]), self.store,
                              self.work_dir)

    def _build_workers(self) -> list[Worker]:
        workers = []
        for number, instance in enumerate(self.config.profiles[self.profile]):
            model = self.config.models[instance.model]
            handlers = {pool: self._handler(pool, instance, model) for pool in model.pools}
            workers.append(Worker(self, f"{instance.model}-{number}", handlers))
        return workers

    def heartbeat(self) -> dict:
        info = {"hostname": platform.node(), "boot_id": self.boot_id, "profile": self.profile,
                "pools": profiles.capacity(self.config, self.profile),
                "profiles": {name: profiles.capacity(self.config, name) for name in self.config.profiles},
                "telemetry": self.telemetry_fn(), "inflight": self.inflight}
        reply = self.client.heartbeat(self.config.node_id, info)
        self.draining, self.switch_to = bool(reply.get("drain")), reply.get("switch_to")
        return reply

    def step(self) -> int:
        """One claim attempt per worker, sequentially (tests and single-threaded runs)."""
        if self.pending or self.switch_to:
            self.maybe_switch()
            return 0
        return 0 if self.draining else sum(worker.step() for worker in self.workers)

    def maybe_switch(self) -> bool:
        """Switch GPU profile once drained (or finish an interrupted switch), then report back."""
        target = self.pending or self.switch_to
        if not target or self.inflight or time.monotonic() < self._retry_at:
            return False
        if target not in self.config.profiles:
            log.error("control plane asked for unknown profile %s", target)
            return False
        if not self._apply_profile(target):
            return False
        self.pending = None
        try:
            self.heartbeat()
        except httpx.HTTPError as exc:  # the main loop heartbeats again shortly
            log.warning("heartbeat after profile switch failed: %s", exc)
        return True

    def _apply_profile(self, target: str) -> bool:
        """Run this node's switch command for target, recording progress so a crash can resume it."""
        command = self.config.switch_command.format(profile=target, compose_dir=self.config.compose_dir)
        self._save_state({"profile": self.profile, "switching_to": target})
        try:
            if command:
                result = self.runner(shlex.split(command), capture_output=True, text=True, timeout=3600)
                if result.returncode != 0:
                    raise RuntimeError((result.stderr or "")[-2000:] or f"exit code {result.returncode}")
            self.profile = target
            self.workers = self._build_workers()
            if command and self._override is None and not self._wait_healthy():
                log.warning("profile %s: some engines were not healthy after %ss", target, self.health_timeout_s)
        except Exception as exc:  # stay drained and retry with back-off; never die half-way through a switch
            self._switch_failures += 1
            self._retry_at = time.monotonic() + min(600.0, 30.0 * 2 ** (self._switch_failures - 1))
            log.error("profile switch to %s failed (attempt %d): %s", target, self._switch_failures, exc)
            return False
        self._switch_failures, self._retry_at = 0, 0.0
        self._save_state({"profile": target})
        return True

    def _load_state(self) -> dict:
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def _save_state(self, state: dict) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        partial = self.state_path.with_name(self.state_path.name + ".part")
        partial.write_text(json.dumps(state), encoding="utf-8")
        os.replace(partial, self.state_path)

    def _wait_healthy(self) -> bool:
        deadline = time.monotonic() + self.health_timeout_s
        for instance in self.config.profiles[self.profile]:
            model = self.config.models[instance.model]
            if model.backend.kind.startswith("simulated"):
                continue
            url = f"http://127.0.0.1:{instance.port}{model.health_path}"
            while True:
                try:
                    if httpx.get(url, timeout=5).status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                if time.monotonic() >= deadline:
                    return False
                time.sleep(10)
        return True

    def serve_forever(self, stop: threading.Event | None = None) -> None:
        stop = stop or threading.Event()
        while not stop.is_set():
            try:
                self.heartbeat()
            except httpx.HTTPError as exc:
                log.warning("heartbeat failed: %s", exc)
                stop.wait(self.config.poll_s)
                continue
            if self.pending or self.switch_to:
                self.maybe_switch()
                stop.wait(self.config.poll_s)
                continue
            generation = threading.Event()
            threads = [threading.Thread(target=self._loop, args=(w, stop, generation), name=w.name, daemon=True)
                       for w in self.workers]
            for thread in threads:
                thread.start()
            while not stop.is_set() and not self.switch_to:
                stop.wait(self.config.heartbeat_s)
                try:
                    self.heartbeat()
                except httpx.HTTPError as exc:
                    log.warning("heartbeat failed: %s", exc)
            generation.set()
            for thread in threads:
                thread.join()

    def _loop(self, worker: Worker, stop: threading.Event, generation: threading.Event) -> None:
        while not (stop.is_set() or generation.is_set()):
            busy = False
            if not self.draining:
                try:
                    busy = worker.step()
                except httpx.HTTPError as exc:
                    log.warning("%s: control plane unreachable: %s", worker.name, exc)
            if not busy:
                stop.wait(self.config.poll_s)
