"""Explicit live Image API experiment; write requests and results under tests/."""

from __future__ import annotations

import argparse
import base64
import json
import sys
import time
from contextlib import ExitStack
from datetime import datetime, timezone
from pathlib import Path

import httpx
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from imagepipe.client import _load_key
from imagepipe.delivery import deliver_image


def main() -> int:
    args = _arguments()
    prompt, references, delivery_size = _inputs(args)
    payload = {
        "model": args.model,
        "prompt": prompt,
        "size": args.size,
        "quality": args.quality,
        "n": 1,
        "output_format": "png",
        "background": "opaque",
    }
    if args.complexity:
        payload["complexity"] = args.complexity
    if args.input_fidelity:
        payload["input_fidelity"] = args.input_fidelity
    endpoint = "/v1/images/edits" if references else "/v1/images/generations"
    request = {
        "url": "https://api.openai.com" + endpoint,
        "encoding": "multipart/form-data" if references else "application/json",
        "payload": payload,
        "references_in_upload_order": references,
        "delivery_size": list(delivery_size),
    }
    args.output.mkdir(parents=True, exist_ok=False)
    _write_json(args.output / "request.json", request)
    if args.dry_run:
        print(json.dumps({"dry_run": True, "request_file": str(args.output / "request.json")}, ensure_ascii=False))
        return 0

    key = _load_key(args.env_file, args.key_name)
    report = {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "requested_model": args.model,
        "quality": args.quality,
        "requested_size": args.size,
        "reference_count": len(references),
        "endpoint": endpoint,
        "success": False,
    }
    started = time.monotonic()
    print(json.dumps({"submitting": True, "model": args.model, "quality": args.quality,
                      "reference_count": len(references), "output": str(args.output)}), flush=True)
    try:
        with httpx.Client(timeout=httpx.Timeout(args.timeout, connect=30)) as client, ExitStack() as stack:
            headers = {"Authorization": "Bearer " + key}
            if references:
                files = [
                    ("image[]", (Path(ref["path"]).name,
                                 stack.enter_context(Path(ref["path"]).open("rb")), ref["mime_type"]))
                    for ref in references
                ]
                response = client.post(request["url"], headers=headers,
                                       data={k: str(v) for k, v in payload.items()}, files=files)
            else:
                response = client.post(request["url"], headers=headers, json=payload)
        report.update(http_status=response.status_code, request_id=response.headers.get("x-request-id"),
                      api_seconds=round(time.monotonic() - started, 3))
        if not response.is_success:
            raise RuntimeError(response.text.replace(key, "[REDACTED]")[:2000])
        result = response.json()
        original = args.output / "original.png"
        original.write_bytes(base64.b64decode(result["data"][0]["b64_json"]))
        metadata = {k: v for k, v in result.items() if k != "data"}
        metadata["data"] = [{k: v for k, v in item.items() if k != "b64_json"}
                            for item in result["data"]]
        _write_json(args.output / "response.json", metadata)
        report["response_metadata"] = metadata
        report["estimated_cost_usd"] = _cost(args.model, result.get("usage", {}))
        report["delivery"] = deliver_image(original, args.output / "delivered.png", *delivery_size)
        report.update(success=True, original=str(original), delivered=str(args.output / "delivered.png"))
    except (httpx.HTTPError, OSError, ValueError, KeyError, RuntimeError) as error:
        report.update(error_type=type(error).__name__, error=str(error).replace(key, "[REDACTED]")[:2000])
    report["elapsed_seconds"] = round(time.monotonic() - started, 3)
    _write_json(args.output / "report.json", report)
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
    return 0 if report["success"] else 1


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prompt-file", type=Path, required=True)
    parser.add_argument("--reference", type=Path, action="append", default=[])
    parser.add_argument("--model", default="gpt-image-2.6-sunburst")
    parser.add_argument("--quality", choices=["low", "medium", "high", "xhigh", "max", "auto"], default="max")
    parser.add_argument("--size", default="1152x2048")
    parser.add_argument("--complexity", choices=["low", "medium", "high"])
    parser.add_argument("--input-fidelity", choices=["low", "high"])
    parser.add_argument("--delivery-size", default="720x1280")
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    parser.add_argument("--key-name", default="OPENAI")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeout", type=float, default=900)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def _inputs(args: argparse.Namespace) -> tuple[str, list[dict], tuple[int, int]]:
    args.output = args.output.resolve()
    if not args.output.is_relative_to(ROOT / "tests"):
        raise ValueError("Experiment output must be inside the repository's tests directory")
    prompt = args.prompt_file.read_text(encoding="utf-8").strip()
    if not 1 <= len(prompt) <= 32000:
        raise ValueError("The prompt must contain 1 through 32000 characters")
    if len(args.reference) > 16:
        raise ValueError("The Image API accepts at most 16 reference images")
    references = []
    for path in args.reference:
        path = path.resolve()
        byte_count = path.stat().st_size
        if byte_count >= 50_000_000:
            raise ValueError(f"Reference image must be smaller than 50 MB: {path}")
        with Image.open(path) as image:
            if image.format not in {"PNG", "JPEG", "WEBP"}:
                raise ValueError(f"Reference must be PNG, JPEG, or WebP: {path}")
            references.append({"path": str(path), "size": list(image.size), "bytes": byte_count,
                               "mime_type": Image.MIME[image.format]})
    width, height = (int(value) for value in args.delivery_size.split("x"))
    if min(width, height) <= 0:
        raise ValueError("Delivery dimensions must be positive")
    return prompt, references, (width, height)


def _cost(model: str, usage: dict) -> dict | None:
    # Standard token rates checked on 2026-11-05; estimate assumes uncached inputs.
    if model.startswith("gpt-image-2"):
        text_rate, image_rate, output_rate = 5, 8, 30
    elif model == "gpt-image-1.5":
        text_rate, image_rate, output_rate = 5, 8, 32
    elif model == "gpt-image-1-mini":
        text_rate, image_rate, output_rate = 2, 2.5, 8
    else:
        return None
    details = usage.get("input_tokens_details", {})
    if not {"text_tokens", "image_tokens"} <= details.keys() or "output_tokens" not in usage:
        return None
    components = {
        "text_input": details["text_tokens"] * text_rate / 1_000_000,
        "image_input": details["image_tokens"] * image_rate / 1_000_000,
        "image_output": usage["output_tokens"] * output_rate / 1_000_000,
    }
    return {**components, "total": round(sum(components.values()), 6),
            "basis": "Standard USD token rates, 2026-11-05; estimate, not an invoice"}


def _write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
