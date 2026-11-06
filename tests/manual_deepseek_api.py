"""Opt-in live check: PYTHONPATH=. .venv/bin/python tests/manual_deepseek_api.py."""

import argparse
import asyncio
import json
import time
from pathlib import Path

import httpx

from imagepipe.client import _load_key
from scriptpipe.llm import DEFAULT_ENDPOINT, GLMClient, write_json


async def probe(args):
    endpoint = args.endpoint.rstrip("/")
    key = _load_key(args.env, args.key_name)
    args.output.mkdir(parents=True, exist_ok=True)
    report = {"endpoint": endpoint, "model": args.model, "key_name": args.key_name,
              "reasoning_effort": args.reasoning_effort, "thinking": {"type": "enabled"},
              "http_statuses": [], "scope": "Small API and existing scriptpipe client compatibility probe"}
    started = time.monotonic()
    try:
        client = GLMClient(key, args.output / "api", endpoint, args.model,
                           concurrency=1, reasoning_effort=args.reasoning_effort)
        async def record_response(response):
            report["http_statuses"].append(response.status_code)
        client.http.event_hooks["response"] = [record_response]
        try:
            result = await asyncio.wait_for(client.complete(
                "small_state_tracking", "Return only JSON with location (one of 桌上, 林青手中, 周宁手中), last_holder (a person's name), and ok=true. Read the events in order.",
                {"events": ["林青把蓝色盒子交给周宁。", "周宁把蓝色盒子放到会议室桌上。", "林青离开会议室。"],
                 "question": "蓝色盒子现在在哪里，谁最后持有它？"},
                temperature=0.7,
            ), timeout=120)
            report["result"] = result
            report["success"] = result == {"location": "桌上", "last_holder": "周宁", "ok": True}
            response_paths = sorted((args.output / "api").glob("*.response.json"))
            if response_paths:
                metadata = json.loads(response_paths[-1].read_text(encoding="utf-8"))
                report.update({name: metadata.get(name) for name in
                               ("served_model", "finish_reason", "usage", "reasoning_characters")})
                report["chat_http_status"] = 200
        finally:
            await client.close()
    except (httpx.HTTPError, RuntimeError, ValueError, TimeoutError) as error:
        report.update(success=False, error=str(error).replace(key, "[REDACTED]") or type(error).__name__)
    report["elapsed_seconds"] = round(time.monotonic() - started, 3)
    write_json(args.output / "report.json", report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report.get("success") else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--endpoint", default=DEFAULT_ENDPOINT)
    parser.add_argument("--model", default="deepseek-v4.1-flash")
    parser.add_argument("--key-name", default="DEEPSEEK-KEY")
    parser.add_argument("--reasoning-effort", choices=("low", "high", "max"), default="high")
    parser.add_argument("--env", type=Path, default=Path(".env"))
    parser.add_argument("--output", type=Path, default=Path("tmp/ark_deepseek_api_probe_20261103"))
    raise SystemExit(asyncio.run(probe(parser.parse_args())))
