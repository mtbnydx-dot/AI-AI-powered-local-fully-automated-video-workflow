"""HTTP API. Worker endpoints serve B200 nodes and review workstations; operator endpoints serve dfctl.

Bind it to the machine-room network only. /metrics is unauthenticated for Prometheus scraping.
"""
import logging
import secrets
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel, Field

from .. import __version__
from ..protocol import keys
from ..protocol.package import PackageError, discard, verify
from .controller import ControlError, Controller
from .export import export_package

log = logging.getLogger(__name__)


class Heartbeat(BaseModel):
    hostname: str = ""
    boot_id: str = ""
    profile: str = ""
    pools: dict[str, int] = {}
    profiles: dict[str, dict[str, int]] = {}
    telemetry: dict = {}
    inflight: int = 0


class ClaimRequest(BaseModel):
    node_id: str
    worker: str
    pools: list[str] = Field(min_length=1)
    limit: int = Field(default=1, ge=1, le=16)


class LeaseRequest(BaseModel):
    lease_token: str


class CompleteRequest(LeaseRequest):
    result: dict


class FailRequest(LeaseRequest):
    code: str
    message: str = ""
    retryable: bool = True


class ImportRequest(BaseModel):
    path: str


class DecisionRequest(BaseModel):
    action: Literal["approve", "reject", "retry"]
    operator: str = Field(min_length=1)
    take_id: str | None = None
    plan: dict | None = None
    extra_rounds: int = Field(default=1, ge=0, le=5)
    note: str = ""


class PauseRequest(BaseModel):
    reason: str = "manual"
    operator: str = "operator"


class ProfileRequest(BaseModel):
    profile: str
    operator: str = "operator"


class ExportRequest(BaseModel):
    media: Literal["approved", "candidates", "none"] = "approved"


def create_app(controller: Controller, worker_token: str, operator_token: str, tick_s: float | None = None) -> FastAPI:
    if not worker_token or not operator_token:
        raise ValueError("worker_token 和 operator_token 都必须设置")
    stop = threading.Event()

    @asynccontextmanager
    async def lifespan(_app):
        if tick_s:
            def loop():
                while not stop.wait(tick_s):
                    try:
                        controller.tick()
                    except Exception:  # keep ticking; the next tick retries
                        log.exception("tick failed")
            threading.Thread(target=loop, name="df-tick", daemon=True).start()
        yield
        stop.set()

    app = FastAPI(title="Drama Factory control plane", version=__version__, lifespan=lifespan)

    def bearer(expected: str):
        def check(authorization: str = Header(default="")):
            if not secrets.compare_digest(authorization.encode(), f"Bearer {expected}".encode()):
                raise HTTPException(401, "未授权")
        return Depends(check)

    worker, operator = bearer(worker_token), bearer(operator_token)

    @app.exception_handler(ControlError)
    async def control_error(_request, exc: ControlError):
        return JSONResponse(status_code=exc.status, content={"detail": str(exc)})

    @app.exception_handler(PackageError)
    async def package_error(_request, exc: PackageError):
        return JSONResponse(status_code=422, content={"detail": "任务包未通过校验",
                                                      "issues": [issue.__dict__ for issue in exc.issues]})

    @app.get("/healthz")
    def healthz():
        return {"ok": True, "version": __version__}

    @app.get("/metrics", response_class=PlainTextResponse)
    def metrics():
        return controller.metrics_text()

    @app.post("/v1/nodes/{node_id}/heartbeat", dependencies=[worker])
    def node_heartbeat(node_id: str, body: Heartbeat):
        return controller.node_heartbeat(node_id, body.model_dump())

    @app.post("/v1/jobs/claim", dependencies=[worker])
    def claim(body: ClaimRequest):
        return {"jobs": controller.claim(body.node_id, body.worker, body.pools, body.limit)}

    @app.post("/v1/jobs/{job_id}/heartbeat", dependencies=[worker])
    def job_heartbeat(job_id: str, body: LeaseRequest):
        return controller.heartbeat_job(job_id, body.lease_token)

    @app.post("/v1/jobs/{job_id}/complete", dependencies=[worker])
    def complete(job_id: str, body: CompleteRequest):
        return controller.complete_job(job_id, body.lease_token, body.result)

    @app.post("/v1/jobs/{job_id}/fail", dependencies=[worker])
    def fail(job_id: str, body: FailRequest):
        return controller.fail_job(job_id, body.lease_token, body.code, body.message, body.retryable)

    @app.post("/v1/packages/import", dependencies=[operator])
    def import_package(body: ImportRequest):
        site = controller.site
        path = Path(body.path).resolve()
        if not any(path.is_relative_to(Path(root).resolve()) for root in site.import_roots):
            raise HTTPException(403, "只能从 site.import_roots 指定的目录导入任务包")
        if not path.exists():
            raise HTTPException(404, f"{path} 不存在")
        trusted = keys.load_trust_dir(Path(site.trusted_keys_dir))
        verified = verify(path, trusted, Path(site.staging_dir), site.routes,
                          site.license.restricted_regions, site.license.authorized_regions)
        try:
            return controller.import_package(verified)  # copies from the verified staged copy
        finally:
            discard(verified)

    @app.get("/v1/status", dependencies=[operator])
    def status():
        return controller.status()

    @app.get("/v1/packages/{package_id}", dependencies=[operator])
    def package(package_id: str):
        return controller.package_view(package_id)

    @app.get("/v1/packages/{package_id}/shots/{shot_id}", dependencies=[operator])
    def shot(package_id: str, shot_id: str):
        return controller.shot_view(package_id, shot_id)

    @app.post("/v1/packages/{package_id}/export", dependencies=[operator])
    def export(package_id: str, body: ExportRequest):
        site = controller.site
        return export_package(controller, package_id, Path(site.export_dir), Path(site.signing_key), body.media)

    @app.get("/v1/human-tasks", dependencies=[operator])
    def human_tasks(status: Literal["open", "resolved"] = "open"):
        return {"tasks": controller.human_tasks(status)}

    @app.post("/v1/human-tasks/{task_id}/decision", dependencies=[operator])
    def decide(task_id: str, body: DecisionRequest):
        return controller.decide(task_id, body.action, body.operator, body.take_id, body.plan, body.extra_rounds,
                                 body.note)

    @app.post("/v1/system/pause", dependencies=[operator])
    def pause(body: PauseRequest):
        controller.pause(body.reason, body.operator)
        return {"paused": True}

    @app.post("/v1/system/resume", dependencies=[operator])
    def resume(body: PauseRequest):
        controller.resume(body.operator)
        return {"paused": False}

    @app.post("/v1/nodes/{node_id}/resume", dependencies=[operator])
    def resume_node(node_id: str, body: PauseRequest):
        controller.resume_node(node_id, body.operator)
        return {"node": node_id, "status": "active"}

    @app.post("/v1/nodes/{node_id}/profile", dependencies=[operator])
    def set_profile(node_id: str, body: ProfileRequest):
        controller.set_profile(node_id, body.profile, body.operator)
        return {"node": node_id, "desired_profile": body.profile}

    return app
