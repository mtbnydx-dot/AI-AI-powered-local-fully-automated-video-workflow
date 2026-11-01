"""Small streaming client for the user-selected GLM endpoint."""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

DEFAULT_ENDPOINT = "https://ark.cn-beijing.volces.com/api/coding/v3"
DEFAULT_MODEL = "glm-5.3"


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_suffix(path.suffix + ".tmp")
    pending.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    pending.replace(path)


def load_key(env_path: Path) -> str:
    """Read only GLMKEY. Never execute the dotenv file or print its contents."""
    key = os.environ.get("GLMKEY", "").strip()
    if not key and env_path.exists():
        for line in env_path.read_text(encoding="utf-8-sig").splitlines():
            match = re.match(r"^\s*(?:export\s+)?GLMKEY\s*=\s*(.*?)\s*$", line)
            if match:
                value = match.group(1)
                if value[:1] in {"'", '"'} and value[-1:] == value[:1]:
                    key = value[1:-1]
                else:
                    key = value.split(" #", 1)[0].strip()
                break
    if not key:
        raise ValueError("GLMKEY is missing from the environment or the selected .env file")
    return key


def parse_json(content: str) -> dict:
    text = content.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    # The observed endpoint occasionally puts literal newlines inside strings.
    # Preserve those strings; saved JSON will escape them normally on export.
    result = json.loads(text, strict=False)
    if not isinstance(result, dict):
        raise ValueError("Expected a JSON object")
    return result


class GLMClient:
    def __init__(self, key: str, log_dir: Path, endpoint: str = DEFAULT_ENDPOINT,
                 model: str = DEFAULT_MODEL, concurrency: int = 4,
                 reasoning_effort: str = "high"):
        self.key = key
        self.log_dir = log_dir
        self.endpoint = endpoint.rstrip("/")
        self.model = model
        self.reasoning_effort = reasoning_effort
        self.semaphore = asyncio.Semaphore(concurrency)
        self.http = httpx.AsyncClient(timeout=httpx.Timeout(1200, connect=30))

    async def close(self) -> None:
        await self.http.aclose()

    def event(self, kind: str, **fields: Any) -> None:
        self.log_dir.mkdir(parents=True, exist_ok=True)
        row = {"time": datetime.now(timezone.utc).isoformat(), "event": kind, **fields}
        with (self.log_dir / "events.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")
        print(json.dumps(row, ensure_ascii=False), flush=True)

    async def complete(self, name: str, system: str, payload: dict,
                       max_tokens: int = 32768, temperature: float = 0.7,
                       reasoning_effort: str | None = None) -> dict:
        """Save request/answer/usage, stream long thinking calls, retry transient failures."""
        messages = [{"role": "system", "content": system},
                    {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}]
        async with self.semaphore:
            for attempt in range(1, 4):
                stamp = f"{name}.{time.time_ns()}.attempt{attempt}"
                request = {"model": self.model, "messages": messages,
                           "max_tokens": max_tokens, "temperature": temperature,
                           "reasoning_effort": reasoning_effort or self.reasoning_effort,
                           "stream": True, "stream_options": {"include_usage": True}}
                write_json(self.log_dir / f"{stamp}.request.json", request)
                started = time.monotonic()
                self.event("request_started", name=name, attempt=attempt, model=self.model,
                           reasoning_effort=request["reasoning_effort"])
                content: list[str] = []
                reasoning_chars = 0
                usage: dict = {}
                finish_reason = None
                response_id = None
                served_model = None
                last_progress = started
                try:
                    async with self.http.stream(
                        "POST", self.endpoint + "/chat/completions", json=request,
                        headers={"Authorization": "Bearer " + self.key},
                    ) as response:
                        if response.status_code != 200:
                            body = (await response.aread()).decode("utf-8", "replace")
                            body = body.replace(self.key, "[REDACTED]")
                            raise RuntimeError(f"HTTP {response.status_code}: {body[:1500]}")
                        async for line in response.aiter_lines():
                            if not line.startswith("data:"):
                                continue
                            data = line[5:].strip()
                            if data == "[DONE]":
                                break
                            if not data:
                                continue
                            packet = json.loads(data)
                            if packet.get("error"):
                                raise RuntimeError(str(packet["error"]))
                            response_id = packet.get("id", response_id)
                            served_model = packet.get("model", served_model)
                            if packet.get("usage"):
                                usage = packet["usage"]
                            for choice in packet.get("choices", []):
                                delta = choice.get("delta", {})
                                if delta.get("content"):
                                    content.append(delta["content"])
                                reasoning_chars += len(delta.get("reasoning_content") or "")
                                finish_reason = choice.get("finish_reason") or finish_reason
                            if time.monotonic() - last_progress > 45:
                                self.event("request_streaming", name=name,
                                           elapsed_seconds=round(time.monotonic() - started, 1),
                                           content_characters=sum(map(len, content)),
                                           reasoning_characters=reasoning_chars)
                                last_progress = time.monotonic()
                    answer = "".join(content)
                    metadata = {"id": response_id, "model": self.model,
                                "served_model": served_model,
                                "finish_reason": finish_reason, "usage": usage,
                                "elapsed_seconds": round(time.monotonic() - started, 2),
                                "reasoning_characters": reasoning_chars, "content": answer}
                    write_json(self.log_dir / f"{stamp}.response.json", metadata)
                    if finish_reason == "length":
                        max_tokens = min(max_tokens * 2, 96000)
                        raise ValueError("Response hit output limit; retrying with a larger budget")
                    result = parse_json(answer)
                    self.event("request_finished", name=name, attempt=attempt,
                               elapsed_seconds=metadata["elapsed_seconds"], usage=usage,
                               content_characters=len(answer), reasoning_characters=reasoning_chars)
                    return result
                except (httpx.HTTPError, RuntimeError, ValueError, KeyError) as exc:
                    detail = str(exc).replace(self.key, "[REDACTED]")
                    write_json(self.log_dir / f"{stamp}.error.json", {
                        "error": detail, "elapsed_seconds": round(time.monotonic() - started, 2),
                        "content": "".join(content), "usage": usage,
                    })
                    self.event("request_error", name=name, attempt=attempt, error=detail)
                    if attempt == 3:
                        raise RuntimeError(f"{name} failed after {attempt} attempts: {detail}") from exc
                    if isinstance(exc, (ValueError, json.JSONDecodeError)) and finish_reason != "length":
                        messages.append({"role": "user", "content":
                                         (Path(__file__).resolve().parent.parent / "prompt" / "scriptpipe" / "json_retry.txt").read_text(encoding="utf-8").strip()})
                    await asyncio.sleep(5 * attempt)
        raise RuntimeError("Unreachable")
