import json
from dataclasses import dataclass
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from drama_factory.cluster.config import SiteConfig, StoreConfig, WatchdogConfig
from drama_factory.control.api import create_app
from drama_factory.control.controller import Controller
from drama_factory.demo import Clock, demo_node, drain, scenario_handlers, write_demo_package
from drama_factory.node.agent import ControlClient, NodeAgent
from drama_factory.protocol import keys
from drama_factory.protocol.manifest import Manifest
from drama_factory.protocol.package import seal
from drama_factory.storage import FileStore
from drama_factory.store.db import make_sessions

OPERATOR = {"Authorization": "Bearer o"}
WORKER = {"Authorization": "Bearer w"}
PACKAGE = "DEMO_EP01_V1"


def edit_manifest(package_dir: Path, mutate) -> Manifest:
    path = package_dir / "manifest.json"
    data = json.loads(path.read_text("utf-8"))
    mutate(data)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return Manifest.model_validate(data)


def shot(data: dict, shot_id: str) -> dict:
    return next(s for s in data["shots"] if s["shot_id"] == shot_id)


def perfect_probe(payload: dict) -> dict:
    return {"decodable": True, "width": payload["width"], "height": payload["height"], "fps": 24.0,
            "duration_s": payload["frames"] / 24, "has_audio": True, "black_s": 0.0, "longest_freeze_s": 0.0,
            "silence_s": 0.0, "max_volume_db": -6.0}


@pytest.fixture
def package_dir(tmp_path) -> Path:
    return write_demo_package(tmp_path / "inbox" / PACKAGE)


@pytest.fixture
def manifest(package_dir) -> Manifest:
    return Manifest.model_validate_json((package_dir / "manifest.json").read_bytes())


@pytest.fixture
def studio(tmp_path):
    """(studio private key, site trust dict) with the studio key already trusted."""
    keys.generate(tmp_path / "keys" / "studio")
    keys.generate(tmp_path / "keys" / "site")
    (tmp_path / "trusted_keys").mkdir()
    (tmp_path / "trusted_keys" / "studio.pub").write_bytes((tmp_path / "keys" / "studio.pub").read_bytes())
    return keys.load_private(tmp_path / "keys" / "studio.key"), keys.load_trust_dir(tmp_path / "trusted_keys")


@dataclass
class Harness:
    controller: Controller
    http: TestClient
    agent: NodeAgent
    clock: Clock
    site: SiteConfig
    store: FileStore
    package: Path

    def import_package(self):
        return self.http.post("/v1/packages/import", json={"path": str(self.package)}, headers=OPERATOR)

    def run(self) -> None:
        drain(self.controller, self.agent, self.clock)

    def shots(self) -> dict:
        view = self.http.get(f"/v1/packages/{PACKAGE}", headers=OPERATOR).json()
        return {s["shot_id"]: s for s in view["shots"]}

    def shot(self, shot_id: str) -> dict:
        return self.http.get(f"/v1/packages/{PACKAGE}/shots/{shot_id}", headers=OPERATOR).json()

    def tasks(self) -> dict:
        tasks = self.http.get("/v1/human-tasks", headers=OPERATOR).json()["tasks"]
        return {t["shot"].split("/")[-1]: t for t in tasks}


@pytest.fixture
def harness(tmp_path, studio, package_dir):
    clients = []

    def make(mutate=None, handlers=None, node=None, **site_fields) -> Harness:
        if mutate:
            edit_manifest(package_dir, mutate)
        seal(package_dir, studio[0])
        site_fields.setdefault("watchdog", WatchdogConfig(min_disk_free_gb=0))
        site = SiteConfig(site_id="test", database_url="sqlite://", store=StoreConfig(root=str(tmp_path / "store")),
                          trusted_keys_dir=str(tmp_path / "trusted_keys"), signing_key=str(tmp_path / "keys" / "site.key"),
                          staging_dir=str(tmp_path / "staging"), export_dir=str(tmp_path / "exports"),
                          import_roots=[str(tmp_path / "inbox")], worker_token="w", operator_token="o", **site_fields)
        clock, store = Clock(), FileStore(tmp_path / "store")
        controller = Controller(make_sessions("sqlite://"), store, site, clock=clock)
        http = TestClient(create_app(controller, "w", "o"))
        http.__enter__()
        clients.append(http)
        node = node or demo_node(tmp_path / "store", tmp_path / "work")
        agent = NodeAgent(node, ControlClient("http://testserver", "w", client=http), store=store,
                          handlers=handlers or scenario_handlers(node, store, render=False),
                          telemetry_fn=lambda: {"gpus": [], "disks": {}}, lease_interval=None,
                          runner=lambda *a, **k: None)
        return Harness(controller, http, agent, clock, site, store, package_dir)

    yield make
    for http in clients:
        http.__exit__(None, None, None)
