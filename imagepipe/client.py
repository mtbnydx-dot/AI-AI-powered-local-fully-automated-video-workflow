"""Async client for the persistent Qwen image job API."""

from __future__ import annotations

import asyncio
import mimetypes
import os
import re
from contextlib import ExitStack
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urljoin, urlsplit

import httpx

if TYPE_CHECKING:
    from .models import ImageConfig, ImageTask


class ImageClient:
    def __init__(self, config: ImageConfig):
        self.config = config
        self.base_url = config.api.base_url.rstrip("/")
        self.key = _load_key(Path(config.api.env_file), config.api.key_name)
        self.semaphore = asyncio.Semaphore(config.scheduler.http_concurrency)
        self.http = httpx.AsyncClient(
            timeout=httpx.Timeout(config.api.request_timeout_seconds, connect=30),
            limits=httpx.Limits(
                max_connections=config.scheduler.http_concurrency,
                max_keepalive_connections=config.scheduler.http_concurrency,
            ),
            follow_redirects=True,
        )

    async def __aenter__(self) -> ImageClient:
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.http.aclose()

    async def submit(self, task: ImageTask, references: list[Path]) -> dict:
        generation = self.config.generation
        # The API requires 32-pixel multiples; delivery restores the configured size.
        native_width = max(256, ((generation.width + 31) // 32) * 32)
        native_height = max(256, ((generation.height + 31) // 32) * 32)
        payload = {
            "model": generation.model,
            "prompt": task.prompt,
            "size": f"{native_width}x{native_height}",
            "steps": generation.steps,
            "n": 1,
            "seed": task.seed,
        }
        if task.mode == "text":
            response = await self._request("POST", "/v1/images/jobs", json=payload)
        else:
            endpoint = {"edit": "/v1/images/edits", "reference": "/v1/images/references"}[task.mode]
            with ExitStack() as stack:
                # Repeated fields preserve the reference numbering used by the prompt.
                files = [
                    ("image", (path.name, stack.enter_context(path.open("rb")),
                               mimetypes.guess_type(path.name)[0] or "application/octet-stream"))
                    for path in references
                ]
                response = await self._request(
                    "POST", endpoint,
                    data={key: str(value) for key, value in payload.items()},
                    files=files,
                )
        return response.json()

    async def status(self, job_id: str) -> dict:
        response = await self._request("GET", f"/v1/images/jobs/{job_id}")
        return response.json()

    async def results(self, job_id: str) -> dict:
        response = await self._request("GET", f"/v1/images/jobs/{job_id}/results")
        return response.json()

    async def download(self, url: str, destination: Path) -> None:
        response = await self._request("GET", url)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(response.content)

    async def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        url = urljoin(self.base_url + "/", path)
        api_origin = urlsplit(self.base_url)
        target = urlsplit(url)
        headers = {}
        if (target.scheme, target.netloc) == (api_origin.scheme, api_origin.netloc):
            headers["Authorization"] = "Bearer " + self.key
        async with self.semaphore:
            response = await self.http.request(method, url, headers=headers, **kwargs)
        if not response.is_success:
            detail = response.text.replace(self.key, "[REDACTED]")[:2000]
            raise httpx.HTTPStatusError(
                f"HTTP {response.status_code} {method} {path}: {detail}",
                request=response.request, response=response,
            )
        return response


def _load_key(env_file: Path, key_name: str) -> str:
    key = os.environ.get(key_name, "").strip()
    if not key and env_file.exists():
        pattern = re.compile(r"^\s*(?:export\s+)?" + re.escape(key_name) + r"\s*=\s*(.*?)\s*$")
        for line in env_file.read_text(encoding="utf-8-sig").splitlines():
            match = pattern.match(line)
            if match:
                value = match.group(1)
                if value[:1] in {"'", '"'} and value[-1:] == value[:1]:
                    key = value[1:-1]
                else:
                    key = value.split(" #", 1)[0].strip()
                break
    if not key:
        raise ValueError(f"{key_name} is missing from the environment or {env_file}")
    return key
