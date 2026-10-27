"""Control-plane state machine.

Every transition runs in one database transaction under a process-wide lock and is triggered by one of:
a package import, a worker reporting a job, a human decision, or the periodic tick (lease expiry,
watchdog, profile planner). All state lives in the database, so a restarted server simply resumes.
The server must run as a single process (uvicorn --workers 1).

Shot lifecycle: blocked -> generating -> reviewing -> (repairing -> next round)* -> approved,
or -> needs_human (final check / escalation) -> approved | rejected | retry.
Tables have no ORM relationships, so parent rows are flushed explicitly before their children.
"""
import logging
import secrets
import threading
import time
import uuid
from collections import Counter
from contextlib import contextmanager

from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from ..cluster import planner
from ..cluster.config import SiteConfig
from ..policy import repair, review, routing, taxonomy
from ..policy.repair import RepairContext, RepairPlan
from ..policy.review import Finding, ReviewReport
from ..policy.spec import TakeSpec, seed_for
from ..protocol import h3
from ..protocol.manifest import Manifest, Shot, frames_of
from ..protocol.package import CHECKSUMS, SIGNATURE, VerifiedPackage
from ..store import models as db
from . import watchdog

log = logging.getLogger(__name__)
OPEN_TAKE = ("queued", "generating", "reviewing")
ACTIVE_JOB = ("queued", "leased")


class ControlError(Exception):
    status = 400


class NotFound(ControlError):
    status = 404


class Conflict(ControlError):
    status = 409


class Invalid(ControlError):
    status = 422


class Controller:
    def __init__(self, sessions: sessionmaker, store, site: SiteConfig, clock=time.time):
        self.sessions, self.store, self.site, self.clock = sessions, store, site, clock
        self.lock = threading.RLock()
        self._manifests: dict[str, tuple[str, Manifest]] = {}

    @contextmanager
    def tx(self):
        with self.lock, self.sessions.begin() as session:
            yield session

    # ---- packages -------------------------------------------------------------------------------

    def import_package(self, verified: VerifiedPackage) -> dict:
        manifest = verified.manifest
        with self.tx() as s:
            existing = s.get(db.Package, manifest.package_id)
            if existing:
                if existing.digest == verified.digest:
                    return {"package_id": existing.id, "status": "already_imported"}
                raise Conflict(f"包 {manifest.package_id} 已导入且内容不同；修改内容请提升 version 并使用新的 package_id")
            try:
                for shot in manifest.shots:
                    routing.initial_route(shot, self.site.routes, frames_of(shot), manifest.automation.max_route)
            except ValueError as exc:
                raise Invalid(str(exc)) from exc
        prefix = f"packages/{manifest.package_id}"
        for rel in [*verified.files, CHECKSUMS, SIGNATURE]:
            self.store.put(verified.root / rel, f"{prefix}/{rel}")
        with self.tx() as s:
            if s.get(db.Package, manifest.package_id):
                raise Conflict(f"包 {manifest.package_id} 正在被并发导入")
            now = self.clock()
            s.add(db.Package(id=manifest.package_id, project_id=manifest.project_id, episode_id=manifest.episode_id,
                             version=manifest.version, digest=verified.digest, key_id=verified.key_id, prefix=prefix,
                             manifest=manifest.model_dump(mode="json"), status="active", imported_at=now))
            s.flush()
            rows = []
            for shot in sorted(manifest.shots, key=lambda x: x.order):
                rows.append(db.Shot(id=f"{manifest.package_id}/{shot.shot_id}", package_id=manifest.package_id,
                                    shot_id=shot.shot_id, seq=shot.order, state="blocked",
                                    difficulty=routing.difficulty(shot),
                                    priority=shot.priority + (10 if shot.importance == "key" else 0), updated_at=now))
            s.add_all(rows)
            s.flush()
            warnings = [str(w) for w in verified.warnings]
            self._event(s, "package_imported", package_id=manifest.package_id, digest=verified.digest,
                        key_id=verified.key_id, shots=len(rows), warnings=warnings)
            for row in rows:
                self._maybe_start(s, row)
            return {"package_id": manifest.package_id, "status": "imported", "shots": len(rows), "warnings": warnings}

    # ---- worker API -----------------------------------------------------------------------------

    def node_heartbeat(self, node_id: str, info: dict) -> dict:
        with self.tx() as s:
            now = self.clock()
            node = s.get(db.Node, node_id)
            profile = str(info.get("profile", ""))
            if node is None:
                node = db.Node(id=node_id, status="active", last_seen=now, profile=profile, desired_profile=profile,
                               profile_since=now)
                s.add(node)
                self._event(s, "node_joined", node=node_id, profile=profile)
            if node.status == "offline":
                node.status = "active"
                self._event(s, "node_back", node=node_id)
            if profile and profile != node.profile:
                node.profile_since = now
                self._event(s, "profile_switched", node=node_id, frm=node.profile, to=profile)
            boot_id = str(info.get("boot_id", ""))[:32]
            if boot_id != node.boot_id:  # agent (re)started: its local node.yaml profile is the new baseline
                node.boot_id, node.desired_profile = boot_id, profile
            node.hostname = str(info.get("hostname", ""))[:128]
            node.profile = profile
            node.desired_profile = node.desired_profile or profile
            node.pools = dict(info.get("pools") or {})
            node.profiles = dict(info.get("profiles") or {})
            node.telemetry = dict(info.get("telemetry") or {})
            node.inflight = int(info.get("inflight") or 0)
            node.last_seen = now
            generation_paused, paused_pools = self._gates(s)
            return {"status": node.status, "drain": node.status != "active",
                    "switch_to": node.desired_profile if node.desired_profile != node.profile else None,
                    "generation_paused": generation_paused, "paused_pools": sorted(paused_pools)}

    def claim(self, node_id: str, worker: str, pools: list[str], limit: int = 1) -> list[dict]:
        with self.tx() as s:
            node = s.get(db.Node, node_id)
            if node is None or node.status != "active" or node.desired_profile != node.profile:
                return []
            generation_paused, paused_pools = self._gates(s)
            allowed = [p for p in pools if p not in paused_pools and not (generation_paused and p.startswith("gen."))]
            if not allowed:
                return []
            now = self.clock()
            query = (select(db.Job).where(db.Job.status == "queued", db.Job.pool.in_(allowed), db.Job.not_before <= now)
                     .order_by(db.Job.priority.desc(), db.Job.created_at, db.Job.id).limit(limit)
                     .with_for_update(skip_locked=True))
            jobs = s.scalars(query).all()
            for job in jobs:
                job.status, job.lease_owner, job.lease_token = "leased", f"{node_id}/{worker}", secrets.token_hex(16)
                job.leased_at, job.lease_expires_at = now, now + self.site.scheduler.lease_s
                job.attempts += 1
                job.updated_at = now
                if job.kind == "generate":
                    take = s.get(db.Take, job.take_id)
                    take.status, take.updated_at = "generating", now
            return [{"job_id": j.id, "kind": j.kind, "pool": j.pool, "stage": j.stage, "lease_token": j.lease_token,
                     "lease_expires_at": j.lease_expires_at, "attempt": j.attempts, "payload": self._lease_payload(j)}
                    for j in jobs]

    def heartbeat_job(self, job_id: str, token: str) -> dict:
        with self.tx() as s:
            job = s.get(db.Job, job_id)
            if job is None or job.lease_token != token:
                return {"ok": False, "cancelled": True}
            if job.status != "leased":
                return {"ok": False, "cancelled": job.status == "cancelled"}
            job.lease_expires_at = self.clock() + self.site.scheduler.lease_s
            return {"ok": True, "cancelled": False, "lease_expires_at": job.lease_expires_at}

    def complete_job(self, job_id: str, token: str, result: dict) -> dict:
        with self.tx() as s:
            job = self._leased(s, job_id, token)
            if job is None:
                return {"accepted": False, "reason": "cancelled"}
            try:
                parsed = self._parse_result(job, result)
            except (ValidationError, ValueError, KeyError, TypeError) as exc:
                self._job_failed(s, job, "BAD_OUTPUT", f"结果格式错误：{exc}", True)
                return {"accepted": False, "reason": "bad_output"}
            now = self.clock()
            job.status, job.result, job.updated_at = "done", result, now
            job.lease_token = None
            self._learn_duration(s, job, now)
            if job.kind == "generate":
                self._on_generated(s, job, result)
            elif job.kind == "review":
                self._on_review(s, job, parsed)
            else:
                self._on_planned(s, job, parsed)
            return {"accepted": True}

    def fail_job(self, job_id: str, token: str, code: str, message: str = "", retryable: bool = True) -> dict:
        with self.tx() as s:
            job = self._leased(s, job_id, token)
            if job is None:
                return {"accepted": False, "reason": "cancelled"}
            self._job_failed(s, job, code, message, retryable)
            return {"accepted": True, "requeued": job.status == "queued"}

    # ---- operator API ---------------------------------------------------------------------------

    def decide(self, task_id: str, action: str, operator: str, take_id: str | None = None,
               plan: dict | None = None, extra_rounds: int = 1, note: str = "") -> dict:
        with self.tx() as s:
            task = s.get(db.HumanTask, task_id)
            if task is None:
                raise NotFound(f"人工任务 {task_id} 不存在")
            if task.status != "open":
                raise Conflict("该人工任务已处理")
            row, now = s.get(db.Shot, task.shot_pk), self.clock()
            if action == "approve":
                take = s.get(db.Take, take_id or task.take_id or row.approved_take_id or "")
                if take is None or take.shot_pk != row.id or not take.output_key:
                    raise Invalid("请指定本镜头一个已生成的 take")
                row.approved_take_id, row.state, row.updated_at = take.id, "approved", now
                self._event(s, "shot_approved", shot=row, take=take, by=operator, note=note)
                self._unblock(s, row)
            elif action == "reject":
                dropped = row.approved_take_id  # no longer feeds L3 continuity or candidate exports
                row.state, row.approved_take_id, row.updated_at = "rejected", None, now
                self._event(s, "shot_rejected", shot=row, by=operator, note=note, dropped_take=dropped)
                for other in self._dependents(s, row):
                    if other.state == "blocked":  # stays blocked until the studio sends a new version
                        self._event(s, "dependency_rejected", shot=other, dependency=row.shot_id)
            elif action == "retry":
                row.bonus_rounds += max(0, extra_rounds)
                ctx = self._repair_context(s, row)
                candidate = RepairPlan.model_validate({**plan, "source": "human"}) if plan else repair.plan_repair(ctx)
                if candidate.terminal:
                    raise Invalid("规则仍判定需要人工处理；请提供具体修复方案（plan）或增加 extra_rounds")
                self._apply(s, row, ctx, candidate)
            else:
                raise Invalid("action 只能是 approve / reject / retry")
            task.status, task.decided_by, task.resolved_at = "resolved", operator, now
            task.decision = {"action": action, "take_id": take_id, "plan": plan, "extra_rounds": extra_rounds, "note": note}
            return {"task_id": task.id, "shot": row.id, "shot_state": row.state}

    def pause(self, reason: str, by: str = "operator") -> None:
        with self.tx() as s:
            self._put_setting(s, "site_pause", {"paused": True, "reason": reason, "since": self.clock(), "by": by})
            self._event(s, "site_paused", reason=reason, by=by)

    def resume(self, by: str = "operator") -> None:
        """Clear site pause and paused pools (and their failure streaks). Node drains are cleared per node."""
        with self.tx() as s:
            self._put_setting(s, "site_pause", {"paused": False})
            self._put_setting(s, "paused_pools", {})
            self._put_setting(s, "streaks", {})
            self._event(s, "site_resumed", by=by)

    def resume_node(self, node_id: str, by: str = "operator") -> None:
        with self.tx() as s:
            node = s.get(db.Node, node_id)
            if node is None:
                raise NotFound(f"节点 {node_id} 不存在")
            node.status, node.status_reason = "active", ""
            self._event(s, "node_resumed", node=node_id, by=by)

    def set_profile(self, node_id: str, profile: str, by: str = "operator") -> None:
        with self.tx() as s:
            node = s.get(db.Node, node_id)
            if node is None:
                raise NotFound(f"节点 {node_id} 不存在")
            if profile not in node.profiles:
                raise Invalid(f"节点 {node_id} 没有 profile {profile}")
            node.desired_profile = profile
            self._event(s, "profile_switch_requested", node=node_id, to=profile, by=by)

    # ---- periodic -------------------------------------------------------------------------------

    def tick(self) -> dict:
        """Periodic maintenance. Each expired job and each step runs in its own transaction and is
        re-read there, so one bad item (malformed telemetry, a config change) cannot stall the rest."""
        now = self.clock()
        report: dict = {"expired": 0, "alarms": [], "errors": []}
        with self.tx() as s:
            expired = list(s.scalars(select(db.Job.id).where(db.Job.status == "leased",
                                                             db.Job.lease_expires_at < now)))
            waiting = list(s.scalars(select(db.Job.id).where(
                db.Job.kind == "plan_repair", db.Job.status == "queued",
                db.Job.created_at < now - self.site.scheduler.director_wait_s)))
        for job_id in expired:
            report["expired"] += bool(self._guarded(report, f"lease {job_id}", self._expire_lease, job_id, now))
        for job_id in waiting:
            self._guarded(report, f"director {job_id}", self._director_timeout, job_id, now)
        report["alarms"] = self._guarded(report, "watchdog", self._watch, now) or []
        if self.site.scheduler.auto_switch_profiles:
            self._guarded(report, "planner", self._plan_profiles, now)
        self._guarded(report, "record", self._record_tick, report, now)
        return report

    def _guarded(self, report: dict, label: str, step, *args):
        try:
            return step(*args)
        except Exception as exc:  # surfaced in /v1/status; the next tick retries
            log.exception("tick step %s failed", label)
            report["errors"].append(f"{label}: {exc}"[:500])
            return None

    def _expire_lease(self, job_id: str, now: float) -> bool:
        with self.tx() as s:
            job = s.get(db.Job, job_id)
            if job is None or job.status != "leased" or job.lease_expires_at is None or job.lease_expires_at >= now:
                return False
            self._job_failed(s, job, "WORKER_LOST", f"租约过期（{job.lease_owner}）", True)
            return True

    def _director_timeout(self, job_id: str, now: float) -> None:
        """The director went away after it was consulted: fall back to the rule plan."""
        with self.tx() as s:
            job = s.get(db.Job, job_id)
            if job is None or job.status != "queued":
                return
            job.status, job.updated_at = "cancelled", now
            row = s.get(db.Shot, job.shot_pk)
            if row.state == "repairing":
                self._event(s, "director_timeout", shot=row, job=job)
                ctx = self._repair_context(s, row)
                self._apply(s, row, ctx, repair.plan_repair(ctx))

    def _watch(self, now: float) -> list[dict]:
        with self.tx() as s:
            nodes = s.scalars(select(db.Node)).all()
            health, capacity = [], Counter()
            for node in nodes:
                stale = now - node.last_seen > self.site.watchdog.heartbeat_timeout_s
                if stale and node.status == "active":
                    node.status = "offline"
                    self._event(s, "node_offline", node=node.id)
                if node.status == "active":
                    capacity.update(node.pools)
                telemetry = node.telemetry if isinstance(node.telemetry, dict) else {}
                health.append(watchdog.NodeHealth(node.id, stale, telemetry.get("gpus") or [],
                                                  telemetry.get("disks") or {}))
            backlog = s.scalar(select(func.count()).select_from(db.Job)
                               .where(db.Job.kind == "review", db.Job.status == "queued")) or 0
            queued = s.execute(select(db.Job.pool, func.count()).where(db.Job.status == "queued").group_by(db.Job.pool))
            starving = {pool: count for pool, count in queued if count and not capacity.get(pool)}
            throttled = bool(self._setting(s, "throttle").get("on"))
            alarms = watchdog.evaluate(health, backlog, self._setting(s, "streaks"), throttled, self.site.watchdog,
                                       starving)
            self._apply_alarms(s, alarms, throttled)
            return [alarm.__dict__ for alarm in alarms]

    def _record_tick(self, report: dict, now: float) -> None:
        with self.tx() as s:
            self._put_setting(s, "tick", {"at": now, "errors": report["errors"][-20:]})

    # ---- views ----------------------------------------------------------------------------------

    def status(self) -> dict:
        with self.tx() as s:
            now = self.clock()
            queues: dict[str, dict[str, int]] = {}
            for pool, state, count in s.execute(select(db.Job.pool, db.Job.status, func.count())
                                                .group_by(db.Job.pool, db.Job.status)):
                queues.setdefault(pool, {})[state] = count
            packages: dict[str, dict[str, int]] = {}
            for package_id, state, count in s.execute(select(db.Shot.package_id, db.Shot.state, func.count())
                                                      .group_by(db.Shot.package_id, db.Shot.state)):
                packages.setdefault(package_id, {})[state] = count
            nodes = [{"id": n.id, "status": n.status, "reason": n.status_reason, "profile": n.profile,
                      "desired_profile": n.desired_profile, "pools": n.pools, "inflight": n.inflight,
                      "last_seen_s": round(now - n.last_seen, 1)} for n in s.scalars(select(db.Node).order_by(db.Node.id))]
            generation_paused, paused_pools = self._gates(s)
            return {"site_id": self.site.site_id, "site_pause": self._setting(s, "site_pause"),
                    "throttle": self._setting(s, "throttle"), "generation_paused": generation_paused,
                    "paused_pools": sorted(paused_pools), "alarms": self._setting(s, "alarms").get("items", []),
                    "tick": self._setting(s, "tick"), "queues": queues, "packages": packages, "nodes": nodes,
                    "open_human_tasks": s.scalar(select(func.count()).select_from(db.HumanTask)
                                                 .where(db.HumanTask.status == "open")) or 0}

    def package_view(self, package_id: str) -> dict:
        with self.tx() as s:
            package = s.get(db.Package, package_id)
            if package is None:
                raise NotFound(f"包 {package_id} 不存在")
            shots = s.scalars(select(db.Shot).where(db.Shot.package_id == package_id).order_by(db.Shot.seq)).all()
            return {"package_id": package.id, "digest": package.digest, "key_id": package.key_id,
                    "shots": [{"shot_id": r.shot_id, "state": r.state, "difficulty": r.difficulty, "round": r.round,
                               "takes": r.takes_used, "route": (r.spec or {}).get("route"),
                               "approved_take_id": r.approved_take_id} for r in shots]}

    def shot_view(self, package_id: str, shot_id: str) -> dict:
        with self.tx() as s:
            row = s.get(db.Shot, f"{package_id}/{shot_id}")
            if row is None:
                raise NotFound(f"镜头 {package_id}/{shot_id} 不存在")
            takes = s.scalars(select(db.Take).where(db.Take.shot_pk == row.id)
                              .order_by(db.Take.round, db.Take.candidate)).all()
            reviews = s.scalars(select(db.Review).where(db.Review.take_id.in_([t.id for t in takes]))
                                .order_by(db.Review.id)).all()
            events = s.scalars(select(db.Event).where(db.Event.shot_pk == row.id).order_by(db.Event.id)).all()
            tasks = s.scalars(select(db.HumanTask).where(db.HumanTask.shot_pk == row.id)).all()
            return {"shot": {"id": row.id, "state": row.state, "round": row.round, "takes_used": row.takes_used,
                             "difficulty": row.difficulty, "approved_take_id": row.approved_take_id, "spec": row.spec},
                    "takes": [{"id": t.id, "round": t.round, "candidate": t.candidate, "status": t.status,
                               "route": t.route, "seed": t.seed, "output_key": t.output_key,
                               "model_version": t.model_version, "gpu_seconds": t.gpu_seconds,
                               "outcome": t.outcome, "plan": t.plan} for t in takes],
                    "reviews": [{"take_id": r.take_id, "level": r.level, "reviewer": r.reviewer, "verdict": r.verdict,
                                 "report": r.report} for r in reviews],
                    "events": [{"ts": e.ts, "kind": e.kind, "take_id": e.take_id, "data": e.data} for e in events],
                    "human_tasks": [_task_view(t) for t in tasks]}

    def human_tasks(self, status: str = "open") -> list[dict]:
        with self.tx() as s:
            tasks = s.scalars(select(db.HumanTask).where(db.HumanTask.status == status)
                              .order_by(db.HumanTask.created_at)).all()
            return [_task_view(t) for t in tasks]

    def metrics_text(self) -> str:
        with self.tx() as s:
            lines = ["# TYPE df_jobs gauge"]
            for pool, state, count in s.execute(select(db.Job.pool, db.Job.status, func.count())
                                                .group_by(db.Job.pool, db.Job.status)):
                lines.append(f'df_jobs{{pool="{pool}",status="{state}"}} {count}')
            lines.append("# TYPE df_shots gauge")
            for state, count in s.execute(select(db.Shot.state, func.count()).group_by(db.Shot.state)):
                lines.append(f'df_shots{{state="{state}"}} {count}')
            lines.append("# TYPE df_takes gauge")
            for route, state, count in s.execute(select(db.Take.route, db.Take.status, func.count())
                                                 .group_by(db.Take.route, db.Take.status)):
                lines.append(f'df_takes{{route="{route}",status="{state}"}} {count}')
            lines.append("# TYPE df_gpu_seconds_total counter")
            for route, seconds in s.execute(select(db.Take.route, func.sum(db.Take.gpu_seconds)).group_by(db.Take.route)):
                lines.append(f'df_gpu_seconds_total{{route="{route}"}} {float(seconds or 0):.1f}')
            lines.append("# TYPE df_node_active gauge")
            for node in s.scalars(select(db.Node)):
                lines.append(f'df_node_active{{node="{node.id}",profile="{node.profile}"}} {int(node.status == "active")}')
            generation_paused, paused_pools = self._gates(s)
            lines += ["# TYPE df_generation_paused gauge", f"df_generation_paused {int(generation_paused)}",
                      "# TYPE df_paused_pools gauge", f"df_paused_pools {len(paused_pools)}"]
            return "\n".join(lines) + "\n"

    # ---- transitions ----------------------------------------------------------------------------

    def _maybe_start(self, s, row: db.Shot) -> None:
        if row.state != "blocked":
            return
        package, manifest, shot = self._context(s, row)
        for dependency in shot.depends_on:
            other = s.get(db.Shot, f"{row.package_id}/{dependency}")
            if other is None or other.approved_take_id is None or other.state == "rejected":
                return
        route = routing.initial_route(shot, self.site.routes, frames_of(shot), manifest.automation.max_route)
        self._start_round(s, row, package, manifest, shot, TakeSpec.initial(shot, route), None)

    def _start_round(self, s, row, package, manifest: Manifest, shot: Shot, spec: TakeSpec,
                     plan: RepairPlan | None) -> None:
        budget = getattr(manifest.retry_policy, row.difficulty)
        _, max_takes = repair.limits(budget, shot, row.bonus_rounds)
        count = max(1, min(budget.candidates_per_round, max_takes - row.takes_used))
        now = self.clock()
        row.round += 1
        row.spec, row.state, row.updated_at = spec.model_dump(mode="json"), "generating", now
        plan_data = plan.model_dump(mode="json") if plan else None
        for candidate in range(count):
            take = db.Take(id=uuid.uuid4().hex, shot_pk=row.id, round=row.round, candidate=candidate, status="queued",
                           route=spec.route, seed=seed_for(package.id, shot.shot_id, row.round, candidate),
                           spec=row.spec, plan=plan_data, created_at=now, updated_at=now)
            s.add(take)
            s.flush()
            row.takes_used += 1
            self._enqueue(s, row, take, "generate", f"gen.{spec.route}", "generate",
                          self._generate_payload(package, manifest, shot, spec, take), budget.max_technical_attempts)
        self._event(s, "round_started", shot=row, round=row.round, route=spec.route, mode=spec.mode,
                    candidates=count, plan=plan_data)

    def _on_generated(self, s, job: db.Job, result: dict) -> None:
        take, row = s.get(db.Take, job.take_id), s.get(db.Shot, job.shot_pk)
        if take.status not in OPEN_TAKE:
            return  # superseded while it was rendering
        take.output_key = str(result["output_key"])
        take.probe = dict(result.get("probe") or {})
        take.gpu_seconds += float(result.get("gpu_seconds") or 0.0)
        take.model_version = str(result.get("model_version") or "")[:128]
        take.status, take.updated_at = "reviewing", self.clock()
        package, manifest, shot = self._context(s, row)
        spec = TakeSpec.model_validate(take.spec)
        l0 = review.l0_report(take.probe, shot, {"width": spec.width, "height": spec.height, "frames": spec.frames},
                              manifest.review_policy)
        self._save_review(s, take, l0)
        levels = review.required_levels(shot, manifest.review_policy) if l0.verdict == "pass" else []
        skipped = [level for level in levels if level not in self.site.review_levels]
        if skipped:
            self._event(s, "review_skipped", shot=row, take=take, levels=skipped)
        levels = [level for level in levels if level in self.site.review_levels]
        for level in levels:
            self._enqueue(s, row, take, "review", review.LEVEL_POOLS[level], level,
                          self._review_payload(s, row, package, manifest, shot, spec, take, level))
        if levels:
            self._refresh_state(s, row)
        else:
            self._judge(s, row, take, pending=[])

    def _on_review(self, s, job: db.Job, report: ReviewReport) -> None:
        take, row = s.get(db.Take, job.take_id), s.get(db.Shot, job.shot_pk)
        if take.status != "reviewing":
            return
        self._save_review(s, take, report)
        pending = s.scalars(select(db.Job).where(db.Job.take_id == take.id, db.Job.kind == "review",
                                                 db.Job.status.in_(ACTIVE_JOB))).all()
        self._judge(s, row, take, pending)

    def _judge(self, s, row, take, pending: list) -> None:
        """Fail fast on the first blocking finding; otherwise wait for every review, then maybe ask L3."""
        package, manifest, shot = self._context(s, row)
        reports = [ReviewReport.model_validate(r.report) for r in
                   s.scalars(select(db.Review).where(db.Review.take_id == take.id).order_by(db.Review.id))]
        names = {c.character_id: c.name for c in manifest.characters}
        outcome = review.verdict(reports, shot, manifest.review_policy, take.id, names)
        if pending and outcome.status != "fail":
            return
        for job in pending:
            job.status, job.updated_at = "cancelled", self.clock()
        if outcome.status == "need_l3" and "L3" not in self.site.review_levels:
            outcome = outcome.model_copy(update={"status": "pass", "needs_human": True})  # a human stands in for L3
        if outcome.status == "need_l3":
            spec = TakeSpec.model_validate(take.spec)
            self._enqueue(s, row, take, "review", review.LEVEL_POOLS["L3"], "L3",
                          self._review_payload(s, row, package, manifest, shot, spec, take, "L3"))
            self._refresh_state(s, row)
            return
        take.status = "passed" if outcome.status == "pass" else "failed"
        take.outcome, take.updated_at = outcome.model_dump(mode="json"), self.clock()
        codes = [f.code for f in outcome.findings if f.severity != "minor"]
        self._event(s, f"take_{take.status}", shot=row, take=take, score=outcome.score, codes=codes)
        technical = any(taxonomy.category(code) in ("technical", "config") for code in codes)
        self._bump_streak(s, take.route, failed=True if technical else (False if take.status == "passed" else None))
        self._round_check(s, row)

    def _round_check(self, s, row) -> None:
        package, manifest, shot = self._context(s, row)
        takes = s.scalars(select(db.Take).where(db.Take.shot_pk == row.id, db.Take.round == row.round)).all()
        passed = [t for t in takes if t.status == "passed"]
        still_open = [t for t in takes if t.status in OPEN_TAKE]
        if passed and (manifest.review_policy.candidate_selection == "first_pass" or not still_open):
            for take in still_open:
                self._supersede(s, row, take)
            best = max(passed, key=lambda t: (t.outcome or {}).get("score", 0.0))
            self._passed(s, row, manifest, shot, best)
        elif still_open:
            self._refresh_state(s, row)
        else:
            self._next_round(s, row)

    def _passed(self, s, row, manifest: Manifest, shot: Shot, take) -> None:
        policy = manifest.review_policy
        final_check = shot.review.human_final_check
        if final_check is None:
            final_check = policy.human_final_check == "all" or (
                policy.human_final_check == "key_shots" and shot.importance == "key")
        row.approved_take_id, row.updated_at = take.id, self.clock()
        if final_check or (take.outcome or {}).get("needs_human"):
            self._human(s, row, take, "final_check" if final_check else "uncertain", None)
        else:
            row.state = "approved"
            self._event(s, "shot_approved", shot=row, take=take, by="machine")
        self._unblock(s, row)

    def _next_round(self, s, row) -> None:
        ctx = self._repair_context(s, row)
        rules = repair.plan_repair(ctx)
        max_rounds, max_takes = repair.limits(ctx.budget, ctx.shot, ctx.bonus_rounds)
        budget_left = ctx.rounds_used < max_rounds and ctx.takes_used < max_takes
        if (budget_left and not ctx.infra_failed and ctx.manifest.automation.director == "llm"
                and self._pool_online(s, "director")):
            row.state, row.updated_at = "repairing", self.clock()
            self._enqueue(s, row, None, "plan_repair", "director", "plan",
                          {"shot": row.id, "brief": repair.director_brief(ctx, rules)})
            self._event(s, "director_consulted", shot=row, round=row.round)
            return
        self._apply(s, row, ctx, rules)

    def _on_planned(self, s, job: db.Job, plan: RepairPlan) -> None:
        row = s.get(db.Shot, job.shot_pk)
        if row.state == "repairing":
            self._apply(s, row, self._repair_context(s, row), plan)

    def _apply(self, s, row, ctx: RepairContext, plan: RepairPlan) -> None:
        if plan.source != "rules":
            problems = repair.check_plan(plan, ctx, human=plan.source == "human")
            if problems:
                self._event(s, "plan_rejected", shot=row, source=plan.source, problems=problems,
                            plan=plan.model_dump(mode="json"))
                if plan.source == "human":
                    raise Invalid("；".join(problems))
                plan = repair.plan_repair(ctx)
        if plan.terminal is None:
            problems = repair.check_plan(plan, ctx, human=plan.source == "human")
            if problems:
                plan = repair.escalation("invalid_plan", "；".join(problems))
        latest = s.scalars(select(db.Take).where(db.Take.shot_pk == row.id)
                           .order_by(db.Take.round.desc(), db.Take.candidate)).first()
        if plan.terminal is not None:
            self._human(s, row, latest, plan.terminal.reason or plan.terminal.action, plan)
            return
        package, manifest, shot = self._context(s, row)
        self._start_round(s, row, package, manifest, shot, repair.apply_plan(ctx.spec, plan, ctx), plan)

    def _job_failed(self, s, job: db.Job, code: str, message: str, retryable: bool) -> None:
        if job.status != "leased":
            return  # only a live lease can fail; a cancelled or finished job was settled elsewhere
        now = self.clock()
        row = s.get(db.Shot, job.shot_pk)
        job.error_code, job.error, job.updated_at = code[:32], (message or "")[:4000], now
        job.lease_token, job.lease_owner, job.lease_expires_at = None, None, None
        self._event(s, "job_failed", shot=row, job=job, pool=job.pool, code=code, message=(message or "")[:500],
                    attempt=job.attempts)
        take = s.get(db.Take, job.take_id) if job.take_id else None
        if retryable and job.attempts < job.max_attempts:
            job.status = "queued"
            job.not_before = now + min(600.0, 20.0 * 2 ** (job.attempts - 1))
            if job.kind == "generate" and take is not None and take.status == "generating":
                take.status = "queued"
            return
        job.status = "failed"
        if job.kind == "generate":
            if take.status in OPEN_TAKE:
                take.status, take.updated_at = "error", now
                take.outcome = {"error": code, "message": (message or "")[:500], "findings": []}
                self._bump_streak(s, take.route, failed=True)
                self._round_check(s, row)
        elif job.kind == "review":
            if take.status == "reviewing":
                # A reviewer that is down must not burn creative budget: record indecision and move on.
                self._on_review(s, job, ReviewReport(level=job.stage, reviewer="unavailable", verdict="uncertain",
                                                     metrics={"error": code}))
        elif row.state == "repairing":  # director unavailable: fall back to the rule plan
            ctx = self._repair_context(s, row)
            self._apply(s, row, ctx, repair.plan_repair(ctx))

    # ---- helpers --------------------------------------------------------------------------------

    def _lease_payload(self, job: db.Job) -> dict:
        """Generation output goes to a per-attempt key, so a cut-off worker that finishes late
        can never overwrite the file a later attempt delivered and review approved."""
        if job.kind != "generate":
            return job.payload
        return {**job.payload, "output_key": attempt_key(job.payload["output_key"], job.attempts)}

    def _dependents(self, s, row) -> list:
        _, manifest, _ = self._context(s, row)
        ids = {x.shot_id for x in manifest.shots if row.shot_id in x.depends_on}
        return [r for r in s.scalars(select(db.Shot).where(db.Shot.package_id == row.package_id)) if r.shot_id in ids]

    def _parse_result(self, job: db.Job, result: dict):
        if job.kind == "generate":
            if not isinstance(result.get("probe", {}), dict):
                raise ValueError("generate 结果需要 probe")
            expected = attempt_key(job.payload["output_key"], job.attempts)
            if result.get("output_key") != expected:
                raise ValueError(f"output_key 必须是本次租约分配的 {expected}")
            return result
        if job.kind == "review":
            report = ReviewReport.model_validate(result)
            if report.level != job.stage:
                raise ValueError(f"审核级别 {report.level} 与任务 {job.stage} 不符")
            return report
        return RepairPlan.model_validate({**result, "source": "director"})

    def _leased(self, s, job_id: str, token: str) -> db.Job | None:
        job = s.get(db.Job, job_id)
        if job is None:
            raise NotFound(f"任务 {job_id} 不存在")
        if job.status == "cancelled":
            return None
        if job.lease_token != token or job.status != "leased":
            raise Conflict("租约已失效：任务已超时被重新分配，或结果已提交")
        return job

    def _repair_context(self, s, row) -> RepairContext:
        package, manifest, shot = self._context(s, row)
        takes = s.scalars(select(db.Take).where(db.Take.shot_pk == row.id)).all()

        def failing(round_no: int) -> list[dict]:
            return [f for t in takes if t.round == round_no for f in (t.outcome or {}).get("findings", [])
                    if f.get("severity") != "minor"]

        current = [t for t in takes if t.round == row.round]
        return RepairContext(
            manifest=manifest, shot=shot, spec=TakeSpec.model_validate(row.spec),
            findings=[Finding.model_validate(f) for f in failing(row.round)],
            history=[sorted({f["code"] for f in failing(n)}) for n in range(1, row.round)],
            rounds_used=row.round, takes_used=row.takes_used,
            budget=getattr(manifest.retry_policy, row.difficulty), routes=self.site.routes,
            infra_failed=bool(current) and all(t.status == "error" for t in current), bonus_rounds=row.bonus_rounds)

    def _human(self, s, row, take, reason: str, plan: RepairPlan | None) -> None:
        now = self.clock()
        row.state, row.updated_at = "needs_human", now
        s.add(db.HumanTask(id=uuid.uuid4().hex, shot_pk=row.id, take_id=take.id if take else None, reason=reason[:32],
                           status="open", proposal=plan.model_dump(mode="json") if plan else None, created_at=now))
        self._event(s, "needs_human", shot=row, take=take, reason=reason, rationale=plan.rationale if plan else "")

    def _unblock(self, s, row) -> None:
        for other in s.scalars(select(db.Shot).where(db.Shot.package_id == row.package_id,
                                                     db.Shot.state == "blocked")).all():
            self._maybe_start(s, other)

    def _supersede(self, s, row, take) -> None:
        take.status, take.updated_at = "superseded", self.clock()
        for job in s.scalars(select(db.Job).where(db.Job.take_id == take.id, db.Job.status.in_(ACTIVE_JOB))):
            job.status = "cancelled"  # lease token kept so the worker learns it on heartbeat/complete
        self._event(s, "take_superseded", shot=row, take=take)

    def _refresh_state(self, s, row) -> None:
        takes = s.scalars(select(db.Take).where(db.Take.shot_pk == row.id, db.Take.round == row.round)).all()
        if any(t.status in ("queued", "generating") for t in takes):
            row.state = "generating"
        elif any(t.status == "reviewing" for t in takes):
            row.state = "reviewing"
        row.updated_at = self.clock()

    def _enqueue(self, s, row, take, kind: str, pool: str, stage: str, payload: dict, max_attempts: int = 3) -> db.Job:
        now = self.clock()
        job = db.Job(id=uuid.uuid4().hex, kind=kind, pool=pool, stage=stage, shot_pk=row.id,
                     take_id=take.id if take else None, status="queued", priority=row.priority, payload=payload,
                     attempts=0, max_attempts=max_attempts, not_before=now, created_at=now, updated_at=now)
        s.add(job)
        return job

    def _generate_payload(self, package, manifest: Manifest, shot: Shot, spec: TakeSpec, take) -> dict:
        assets = {a.asset_id: a for a in manifest.assets}

        def key(asset_id: str | None) -> str | None:
            return f"{package.prefix}/{assets[asset_id].path}" if asset_id else None

        kinds = [assets[a].kind for a in spec.references]
        references = [{"asset_id": a, "kind": k, "token": f"<{t} {n}>", "key": key(a)}
                      for a, k, (t, n) in zip(spec.references, kinds, h3.ref_slots(kinds))]
        return {"take_id": take.id, "package_id": package.id, "shot_id": shot.shot_id, "round": take.round,
                "route": spec.route, "mode": spec.mode, "prompt": h3.render_prompt(spec.prompt),
                "negative_prompt": manifest.negative_prompt, "frames": spec.frames, "fps": h3.FPS,
                "width": spec.width, "height": spec.height, "seed": take.seed,
                "first_frame": key(spec.first_frame), "last_frame": key(spec.last_frame), "references": references,
                "output_key": f"takes/{package.id}/{shot.shot_id}/r{take.round:02d}c{take.candidate}-{take.id[:8]}.mp4"}

    def _review_payload(self, s, row, package, manifest: Manifest, shot: Shot, spec: TakeSpec, take,
                        level: str) -> dict:
        assets = {a.asset_id: a for a in manifest.assets}
        characters = {c.character_id: c for c in manifest.characters}
        scene = next((x for x in manifest.scenes if x.scene_id == shot.scene_id), None)
        if level == "L2.asr":
            refs = [characters[c].voice_asset for c in shot.characters if characters[c].voice_asset]
        else:
            refs = [a for c in shot.characters for a in characters[c].identity_assets]
            refs += [a for a in spec.references if assets[a].kind == "image"]
        payload = {
            "take_id": take.id, "round": take.round, "level": level, "video_key": take.output_key,
            "duration_s": spec.frames / h3.FPS,
            "shot": {"shot_id": shot.shot_id, "importance": shot.importance, "motion": shot.motion,
                     "characters": [{"id": c, "name": characters[c].name, "appearance": characters[c].appearance,
                                     "costume": characters[c].costume, "voice": characters[c].voice}
                                    for c in shot.characters],
                     "scene": scene.description if scene else "",
                     "description": spec.prompt.description, "action": spec.prompt.action,
                     "camera": spec.prompt.camera, "constraints": spec.prompt.constraints,
                     "dialogue": [{"speaker": d.speaker, "speaker_name": characters[d.speaker].name,
                                   "language": d.language, "text": d.text} for d in shot.dialogue]},
            "references": [{"asset_id": a, "kind": assets[a].kind, "role": assets[a].role, "owner": assets[a].owner,
                            "key": f"{package.prefix}/{assets[a].path}"} for a in dict.fromkeys(refs) if a in assets],
        }
        if level == "L3":
            previous = s.scalars(select(db.Shot).where(db.Shot.package_id == row.package_id, db.Shot.seq < row.seq,
                                                       db.Shot.approved_take_id.is_not(None))
                                 .order_by(db.Shot.seq.desc())).first()
            if previous:
                prior = s.get(db.Take, previous.approved_take_id)
                payload["previous"] = {"shot_id": previous.shot_id, "video_key": prior.output_key}
        return payload

    def _save_review(self, s, take, report: ReviewReport) -> None:
        s.add(db.Review(take_id=take.id, level=report.level, reviewer=report.reviewer[:128], verdict=report.verdict,
                        report=report.model_dump(mode="json"), created_at=self.clock()))
        s.flush()

    def _context(self, s, row) -> tuple[db.Package, Manifest, Shot]:
        package = s.get(db.Package, row.package_id)
        cached = self._manifests.get(package.id)
        if cached is None or cached[0] != package.digest:
            cached = (package.digest, Manifest.model_validate(package.manifest))
            self._manifests[package.id] = cached
        manifest = cached[1]
        return package, manifest, next(x for x in manifest.shots if x.shot_id == row.shot_id)

    def _pool_online(self, s, pool: str) -> bool:
        cutoff = self.clock() - self.site.watchdog.heartbeat_timeout_s
        return any(node.pools.get(pool, 0) > 0 for node in
                   s.scalars(select(db.Node).where(db.Node.status == "active", db.Node.last_seen >= cutoff)))

    def _gates(self, s) -> tuple[bool, set[str]]:
        generation_paused = bool(self._setting(s, "site_pause").get("paused") or self._setting(s, "throttle").get("on"))
        return generation_paused, set(self._setting(s, "paused_pools"))

    def _bump_streak(self, s, route: str, failed: bool | None) -> None:
        if failed is None:
            return
        streaks = self._setting(s, "streaks")
        pool = f"gen.{route}"
        streaks[pool] = streaks.get(pool, 0) + 1 if failed else 0
        self._put_setting(s, "streaks", streaks)

    def _learn_duration(self, s, job: db.Job, now: float) -> None:
        if job.leased_at is None:
            return
        stats = self._setting(s, "job_seconds")
        seconds, previous = now - job.leased_at, stats.get(job.pool)
        stats[job.pool] = seconds if previous is None else 0.8 * previous + 0.2 * seconds
        self._put_setting(s, "job_seconds", stats)

    def _apply_alarms(self, s, alarms: list[watchdog.Alarm], was_throttled: bool) -> None:
        now = self.clock()
        self._put_setting(s, "alarms", {"items": [a.__dict__ for a in alarms], "at": now})
        throttle = any(a.action == "throttle" for a in alarms)
        if throttle != was_throttled:
            self._put_setting(s, "throttle", {"on": throttle, "since": now})
            self._event(s, "throttle_on" if throttle else "throttle_off")
        for alarm in alarms:
            target = alarm.scope.partition(":")[2]
            if alarm.action == "drain_node":
                node = s.get(db.Node, target)
                if node is not None and node.status == "active":
                    node.status, node.status_reason = "draining", alarm.message[:255]
                    self._event(s, "node_drained", node=target, code=alarm.code, message=alarm.message)
            elif alarm.action == "pause_site" and not self._setting(s, "site_pause").get("paused"):
                self._put_setting(s, "site_pause", {"paused": True, "reason": alarm.message, "since": now,
                                                    "by": "watchdog"})
                self._event(s, "site_paused", reason=alarm.message, by="watchdog")
            elif alarm.action == "pause_pool":
                pools = self._setting(s, "paused_pools")
                if target not in pools:
                    pools[target] = alarm.message
                    self._put_setting(s, "paused_pools", pools)
                    self._event(s, "pool_paused", pool=target, message=alarm.message)

    def _plan_profiles(self, now: float) -> None:
        """Ask at most one node at a time to re-split its GPUs when backlog imbalance persists."""
        with self.tx() as s:
            active = [n for n in s.scalars(select(db.Node)) if n.status == "active"]
            if any(n.desired_profile != n.profile for n in active):
                return
            seconds = {**self.site.scheduler.job_seconds, **self._setting(s, "job_seconds")}
            backlog = {pool: count * seconds.get(pool, 60.0) for pool, count in s.execute(
                select(db.Job.pool, func.count()).where(db.Job.status == "queued").group_by(db.Job.pool))}
            states, cfg = self._setting(s, "planner"), self.site.scheduler
            for node in active:
                if len(node.profiles) < 2:
                    continue
                others: Counter = Counter()
                for other in active:
                    if other.id != node.id:
                        others.update(other.pools)
                state = planner.PlannerState(**states.get(node.id, {}))
                choice, state = planner.recommend(node.profile, node.profiles, dict(others), backlog, state, now,
                                                  node.profile_since, cfg.switch_sustain_s, cfg.switch_min_dwell_s,
                                                  cfg.switch_gain)
                states[node.id] = state.__dict__
                if choice:
                    node.desired_profile = choice
                    self._event(s, "profile_switch_requested", node=node.id, to=choice, by="planner")
                    break
            self._put_setting(s, "planner", states)

    def _setting(self, s, key: str) -> dict:
        row = s.get(db.Setting, key)
        return dict(row.value) if row else {}

    def _put_setting(self, s, key: str, value: dict) -> None:
        row = s.get(db.Setting, key)
        if row is None:
            s.add(db.Setting(key=key, value=value))
        else:
            row.value = value

    def _event(self, s, kind: str, shot=None, take=None, job=None, package_id: str | None = None, **data) -> None:
        s.add(db.Event(ts=self.clock(), kind=kind, package_id=package_id or (shot.package_id if shot else None),
                       shot_pk=shot.id if shot else None, take_id=take.id if take else None,
                       job_id=job.id if job else None, data=data))


def attempt_key(key: str, attempt: int) -> str:
    stem, dot, suffix = key.rpartition(".")
    return f"{stem}-a{attempt}.{suffix}" if dot else f"{key}-a{attempt}"


def _task_view(task: db.HumanTask) -> dict:
    return {"id": task.id, "shot": task.shot_pk, "take_id": task.take_id, "reason": task.reason, "status": task.status,
            "proposal": task.proposal, "decision": task.decision, "decided_by": task.decided_by,
            "created_at": task.created_at, "resolved_at": task.resolved_at}
