"""Run explicitly to generate one neutral image using the configured remote API."""

import argparse
import asyncio
import json
import re
import sys
import time
from pathlib import Path

import yaml
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from imagepipe.client import ImageClient
from imagepipe.delivery import deliver_image
from imagepipe.models import ImageConfig, ImageTask


async def probe(config: ImageConfig, output: Path) -> dict:
    output.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    report = {
        "base_url": config.api.base_url,
        "requested_generation": config.generation.model_dump(),
        "requests": [],
        "success": False,
    }
    task = ImageTask(
        "neutral_empty_office", "text",
        "An empty modern office meeting room in soft daylight. A wooden table, "
        "four chairs and a large window. Neutral colors, realistic photography, "
        "clear spatial layout, no people, no text, no logos.",
        [], str(output / "delivered.png"), config.generation.seed_base,
    )
    async with ImageClient(config) as client:
        async def record_response(response):
            report["requests"].append({
                "method": response.request.method,
                "path": response.request.url.path,
                "http_status": response.status_code,
            })

        client.http.event_hooks["response"] = [record_response]
        try:
            submission = await client.submit(task, [])
            report["job_id"] = submission["id"]
            report["submission_status"] = submission.get("status")
            print(json.dumps({"submitted": True, "job_id": submission["id"]}), flush=True)
            try:
                response = await client._request("GET", "/openapi.json")
                paths = response.json().get("paths", {})
                report["documented_image_routes"] = {
                    path: list(methods)
                    for path, methods in paths.items() if path.startswith("/v1/images")
                }
            except Exception as error:
                report["metadata_lookup"] = type(error).__name__
            last_status = None
            while True:
                status = await client.status(submission["id"])
                if status["status"] != last_status:
                    last_status = status["status"]
                    print(json.dumps({"status": last_status}), flush=True)
                report["job_status"] = status["status"]
                if status.get("model"):
                    report["reported_model"] = status["model"]
                if status["status"] in {"completed", "failed", "cancelled"}:
                    break
                if time.monotonic() - started > 600:
                    raise TimeoutError("Job still running after 600 seconds; use the recorded job ID to resume polling")
                await asyncio.sleep(config.scheduler.poll_interval_seconds)
            if status["status"] != "completed":
                raise RuntimeError(str(status.get("error") or status["status"]))
            results = await client.results(submission["id"])
            if results.get("model"):
                report["reported_model"] = results["model"]
            original = output / "original.png"
            await client.download(results["data"][0]["url"], original)
            with Image.open(original) as image:
                report["original_size"] = list(image.size)
                report["original_format"] = image.format
            report["delivery"] = deliver_image(
                original, Path(task.target_path), config.generation.width, config.generation.height,
            )
            report["original_path"] = str(original)
            report["delivered_path"] = task.target_path
            report["success"] = True
        except Exception as error:
            message = str(error).replace(client.key, "[REDACTED]")
            report["error"] = re.sub(r"https?://\S+", "[URL]", message)[:2000]
            report["error_type"] = type(error).__name__
    report["elapsed_seconds"] = round(time.monotonic() - started, 3)
    (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("configs/imagepipe.yaml"))
    parser.add_argument("--output", type=Path, default=Path("tmp/qwen_image_api_probe_20261103"))
    args = parser.parse_args()
    config = ImageConfig.model_validate(yaml.safe_load(args.config.read_text()) or {})
    report = asyncio.run(probe(config, args.output.resolve()))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
