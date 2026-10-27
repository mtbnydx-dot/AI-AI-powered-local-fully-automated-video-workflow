"""Result bundle for the trip back to the studio: approved media, review records, failures, cost metrics.

The bundle is sealed with the site key in the same format as packages, so the studio can verify it.
"""
import json
import shutil
import time
from pathlib import Path

from sqlalchemy import select

from ..protocol import h3, keys
from ..protocol.package import seal
from ..store import models as db
from .controller import Controller, Invalid, NotFound


def export_package(controller: Controller, package_id: str, out_dir: Path, signing_key: Path,
                   media: str = "approved") -> dict:
    """media: "approved" (state approved), "candidates" (any machine-passed take) or "none"."""
    if media not in ("approved", "candidates", "none"):
        raise Invalid("media 只能是 approved / candidates / none")
    if not signing_key or not Path(signing_key).is_file():
        raise Invalid("未配置站点签名密钥（site.signing_key），无法封装结果包")
    with controller.tx() as s:
        package = s.get(db.Package, package_id)
        if package is None:
            raise NotFound(f"包 {package_id} 不存在")
        rows = s.scalars(select(db.Shot).where(db.Shot.package_id == package_id).order_by(db.Shot.seq)).all()
        takes = s.scalars(select(db.Take).where(db.Take.shot_pk.in_([r.id for r in rows]))).all()
        tasks = s.scalars(select(db.HumanTask).where(db.HumanTask.shot_pk.in_([r.id for r in rows]))).all()
        digest, notice = package.digest, controller.site.license.notice
        by_id = {t.id: t for t in takes}
        depends = {s["shot_id"]: s.get("depends_on", []) for s in package.manifest["shots"]}
        states = {r.shot_id: r.state for r in rows}
        records, copies = [], []
        for row in rows:
            take = by_id.get(row.approved_take_id or "")
            include = take is not None and (media == "candidates" or (media == "approved" and row.state == "approved"))
            open_task = next((t for t in tasks if t.shot_pk == row.id and t.status == "open"), None)
            last_round = [t for t in takes if t.shot_pk == row.id and t.round == row.round]
            records.append({
                "shot_id": row.shot_id, "order": row.seq, "state": row.state, "difficulty": row.difficulty,
                "rounds": row.round, "takes_used": row.takes_used,
                "selected_take": None if take is None else {
                    "take_id": take.id, "round": take.round, "route": take.route, "seed": take.seed,
                    "model_version": take.model_version, "score": (take.outcome or {}).get("score"),
                    "file": f"media/{row.shot_id}.mp4" if include else None},
                "human_task": None if open_task is None else {
                    "id": open_task.id, "reason": open_task.reason,
                    "rationale": (open_task.proposal or {}).get("rationale", "")},
                "blocked_by": [d for d in depends.get(row.shot_id, []) if states.get(d) != "approved"]
                if row.state == "blocked" else [],
                "last_findings": [{"code": f["code"], "level": f.get("level"), "message": f.get("message", "")}
                                  for t in last_round for f in (t.outcome or {}).get("findings", [])
                                  if f.get("severity") != "minor"],
            })
            if include:
                copies.append((take.output_key, row.shot_id))
        approved = [r for r in rows if r.state == "approved"]
        gpu_hours = sum(t.gpu_seconds for t in takes) / 3600
        minutes = sum(by_id[r.approved_take_id].spec["frames"] / h3.FPS / 60 for r in approved)
        summary = {
            "shots": len(rows),
            "by_state": {state: sum(r.state == state for r in rows) for state in sorted({r.state for r in rows})},
            "takes": len(takes),
            "gpu_hours": round(gpu_hours, 4),
            "approved_minutes": round(minutes, 4),
            "gpu_hours_per_approved_minute": round(gpu_hours / minutes, 4) if minutes else None,
            "first_round_pass_rate": round(sum(by_id[r.approved_take_id].round == 1 for r in approved)
                                           / len(approved), 4) if approved else None,
            "mean_takes_per_approved_shot": round(sum(r.takes_used for r in approved) / len(approved), 3)
            if approved else None,
        }
    stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime(controller.clock()))
    bundle = Path(out_dir) / f"{package_id}-results-{stamp}"
    (bundle / "media").mkdir(parents=True, exist_ok=False)
    (bundle / "reviews").mkdir()
    for key, shot_id in copies:
        shutil.copyfile(controller.store.fetch(key, bundle.parent / ".cache"), bundle / "media" / f"{shot_id}.mp4")
    for row in rows:
        detail = controller.shot_view(package_id, row.shot_id)
        (bundle / "reviews" / f"{row.shot_id}.json").write_text(json.dumps(detail, ensure_ascii=False, indent=2),
                                                                 encoding="utf-8")
    result = {"schema_version": "epr/1", "package_id": package_id, "package_digest": digest,
              "site_id": controller.site.site_id, "exported_at": stamp, "license_notice": notice,
              "summary": summary, "shots": records}
    (bundle / "results_manifest.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    bundle_digest = seal(bundle, keys.load_private(Path(signing_key)))
    return {"path": str(bundle), "digest": bundle_digest, "summary": summary}
