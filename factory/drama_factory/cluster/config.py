"""Site and node configuration (YAML). Secrets are written as "env:NAME" and read from the environment."""
import os
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

from ..protocol.manifest import RouteCaps


def secret(value: str) -> str:
    return os.environ.get(value[4:], "") if value.startswith("env:") else value


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class StoreConfig(Strict):
    kind: Literal["fs", "s3"] = "fs"
    root: str = "./data"  # fs: shared mount; s3: local cache root
    bucket: str = ""
    prefix: str = ""
    endpoint_url: str = ""
    access_key: str = ""
    secret_key: str = ""

    def access_key_value(self) -> str:
        return secret(self.access_key)

    def secret_key_value(self) -> str:
        return secret(self.secret_key)


class LicenseConfig(Strict):
    restricted_regions: list[str] = ["US", "EU", "UK", "KR"]
    authorized_regions: list[str] = []  # only after a separate written grant from MiniMax
    notice: str = ("MiniMax H3 output. Distribution limited to the regions listed in the package; "
                   "H3 outputs must not be used to train or improve non-H3 models.")


class WatchdogConfig(Strict):
    gpu_temp_c: float = 87.0
    ecc_uncorrected: int = 1
    min_disk_free_gb: float = 500.0
    review_backlog_high: int = 200
    review_backlog_low: int = 50
    failure_streak: int = 6
    heartbeat_timeout_s: float = 120.0


class SchedulerConfig(Strict):
    lease_s: float = 300.0
    tick_s: float = 10.0
    auto_switch_profiles: bool = False
    switch_sustain_s: float = 1800.0
    switch_min_dwell_s: float = 7200.0
    switch_gain: float = 0.3
    director_wait_s: float = 600.0  # an unclaimed director request falls back to the rule plan
    job_seconds: dict[str, float] = {"gen.vdn": 60, "gen.fast_ref": 150, "gen.native_ref": 900,
                                     "review.l1": 20, "review.asr": 10, "review.identity": 10,
                                     "review.l3": 60, "director": 30}


DEFAULT_ROUTES = {
    "vdn": RouteCaps(modes=["t2va", "fl2va"], max_frames=345),
    "fast_ref": RouteCaps(modes=["ref2va"], max_frames=345),
    "native_ref": RouteCaps(modes=["ref2va"], max_frames=362),
}


class SiteConfig(Strict):
    site_id: str
    database_url: str = "sqlite:///./factory.db"
    store: StoreConfig = Field(default_factory=StoreConfig)
    trusted_keys_dir: str = "./trusted_keys"
    signing_key: str = ""  # site key that seals exported result bundles
    staging_dir: str = "./staging"
    export_dir: str = "./exports"
    import_roots: list[str] = ["./inbox"]  # packages may only be imported from these folders
    worker_token: str = "env:DF_WORKER_TOKEN"
    operator_token: str = "env:DF_OPERATOR_TOKEN"
    # Review levels this site actually serves. A level missing here is skipped and recorded, never faked;
    # without L3, takes that would need it go to the human queue instead.
    review_levels: list[Literal["L1", "L2.asr", "L2.identity", "L3"]] = ["L1", "L2.asr", "L2.identity", "L3"]
    routes: dict[str, RouteCaps] = Field(default_factory=lambda: dict(DEFAULT_ROUTES))
    license: LicenseConfig = Field(default_factory=LicenseConfig)
    watchdog: WatchdogConfig = Field(default_factory=WatchdogConfig)
    scheduler: SchedulerConfig = Field(default_factory=SchedulerConfig)


class BackendConfig(Strict):
    """simulated / http-video serve gen.* pools; openai-chat serves review.l1 / review.l3 / director;
    openai-asr serves review.asr; simulated-review stands in for any reviewer until it is deployed."""
    kind: Literal["simulated", "http-video", "openai-chat", "openai-asr", "simulated-review"]
    options: dict = {}


class ModelService(Strict):
    """One inference service type. command/image are compose templates; {port} {num_gpus} {devices} expand."""
    pools: list[str]
    image: str = ""
    command: str = ""
    gpus: int = Field(default=1, ge=0)  # full GPUs per instance; 0 = exactly one MIG instance
    vram_gb: float
    backend: BackendConfig
    env: dict[str, str] = {}
    health_path: str = "/health"
    verified: bool = False  # set true after the P0 benchmark on this exact image/command


class Instance(Strict):
    model: str
    devices: list[int | str]  # GPU index, or "7/3g.90gb" / "7/1g.23gb#1" for a MIG instance
    port: int


class NodeConfig(Strict):
    node_id: str
    control_url: str
    token: str = "env:DF_WORKER_TOKEN"
    store: StoreConfig = Field(default_factory=StoreConfig)
    work_dir: str = "./work"
    gpu_model: str = "B200"
    gpu_count: int = 8
    gpu_memory_gb: float = 180.0
    gpu_memory: dict[int, float] = {}  # per-index override for mixed workstations
    mig: dict[int, list[str]] = {}  # declared MIG layout per GPU
    mig_uuids: dict[str, str] = {}  # "7/3g.90gb#0" -> "MIG-..." from `nvidia-smi -L`
    models: dict[str, ModelService]
    profiles: dict[str, list[Instance]]
    profile: str
    switch_command: str = ""  # e.g. "docker compose -p df-engines -f {compose_dir}/{profile}.yml up -d --remove-orphans"
    compose_dir: str = "./compose"
    model_cache: str = "/models"
    disk_paths: list[str] = ["/"]
    poll_s: float = 2.0
    heartbeat_s: float = 15.0


def _load(path: Path) -> dict:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{path} 必须是 YAML 映射")
    return data


def _resolve(base: Path, value: str) -> str:
    """Relative paths in a config file are relative to that file, not to the shell's cwd."""
    if not value or value.startswith(("env:", "sqlite://", "postgresql", "/")) or Path(value).is_absolute():
        return value  # "/..." stays POSIX even when configs are rendered on a Windows workstation
    return str((base / value).resolve())


def load_site(path: Path) -> SiteConfig:
    config = SiteConfig.model_validate(_load(path))
    base = Path(path).resolve().parent
    updates = {key: _resolve(base, getattr(config, key))
               for key in ("trusted_keys_dir", "signing_key", "staging_dir", "export_dir")}
    updates["import_roots"] = [_resolve(base, root) for root in config.import_roots]
    if config.database_url.startswith("sqlite:///./"):
        updates["database_url"] = "sqlite:///" + _resolve(base, config.database_url[len("sqlite:///"):])
    config = config.model_copy(update=updates)
    config.store.root = _resolve(base, config.store.root)
    return config


def load_node(path: Path) -> NodeConfig:
    config = NodeConfig.model_validate(_load(path))
    base = Path(path).resolve().parent
    config.store.root = _resolve(base, config.store.root)
    return config.model_copy(update={key: _resolve(base, getattr(config, key)) for key in ("work_dir", "compose_dir")})
