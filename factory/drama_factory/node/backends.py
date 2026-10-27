"""Engine adapters used by the node agent.

Generation: SimulatedGenerator (real ffmpeg test clip, no GPU) and HttpVideoBackend (async video job API).
Review: SimulatedReviewer, VlmReviewer (OpenAI-compatible chat, e.g. vLLM serving Qwen3-VL / Qwen3.8),
AsrReviewer (OpenAI-compatible /v1/audio/transcriptions) and DirectorClient (repair planning).

H3 request shapes differ between SGLang Diffusion / vLLM-Omni / FastVideo builds, so everything
engine-specific is an option to pin during the P0 benchmark. Nothing here runs package-supplied code.
"""
import base64
import json
import mimetypes
import re
import shutil
import subprocess
import tempfile
import time
import unicodedata
from collections.abc import Callable
from pathlib import Path

import httpx

from ..cluster.config import BackendConfig, secret
from ..policy import taxonomy
from ..policy.repair import RepairPlan
from ..policy.review import ReviewReport


class BackendError(Exception):
    def __init__(self, code: str, message: str, retryable: bool = True):
        super().__init__(message)
        self.code, self.retryable = code, retryable


def classify(message: str) -> str:
    text = message.lower()
    if "out of memory" in text or "outofmemory" in text or re.search(r"\boom\b", text):
        return "OOM"
    if "timeout" in text or "timed out" in text:
        return "TIMEOUT"
    return "ENGINE_ERROR"


def _http_error(exc: httpx.HTTPError) -> BackendError:
    detail = str(exc)
    if isinstance(exc, httpx.HTTPStatusError):
        detail += " " + exc.response.text[:500]
    return BackendError(classify(detail), detail[:1000])


def _cancelled(lease) -> None:
    if getattr(lease, "cancelled", False):
        raise BackendError("CANCELLED", "租约已取消（take 已被替代或租约丢失）", retryable=False)


def _image_part(path: Path) -> dict:
    mime = mimetypes.guess_type(path.name)[0] or "image/jpeg"
    return {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode()}"}}


def parse_json(text: str) -> dict:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.S)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                pass
    raise BackendError("BAD_OUTPUT", f"模型没有返回可解析的 JSON：{text[:300]}")


# ---- generation ------------------------------------------------------------------------------------

class SimulatedGenerator:
    """Stands in for an H3 engine. render=True writes a real H.264/AAC clip of the requested size and length
    (needs ffmpeg) so L0 measures real media; render=False writes a JSON stub that stub_probe() reads."""

    def __init__(self, ffmpeg: str = "ffmpeg", fail: Callable[[dict], str | None] | None = None,
                 model_version: str = "simulated/0", render: bool = True):
        self.ffmpeg, self.fail, self.model_version = ffmpeg, fail, model_version
        self.render = render and shutil.which(ffmpeg) is not None

    def generate(self, payload: dict, inputs: dict[str, Path], out: Path, lease=None) -> dict:
        code = self.fail(payload) if self.fail else None
        if code:
            raise BackendError(code, f"simulated {code}")
        frames, fps = payload["frames"], payload["fps"]
        if self.render:
            command = [self.ffmpeg, "-y", "-v", "error",
                       "-f", "lavfi", "-i", f"testsrc2=size={payload['width']}x{payload['height']}:rate={fps}",
                       "-f", "lavfi", "-i", "sine=frequency=330:sample_rate=48000",
                       "-frames:v", str(frames), "-t", f"{frames / fps:.3f}",
                       "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", str(out)]
            subprocess.run(command, check=True, capture_output=True, timeout=600)
        else:
            fields = {k: payload[k] for k in ("width", "height", "frames", "fps", "seed")}
            out.write_bytes(STUB_HEADER + json.dumps(fields).encode())
        return {"model_version": self.model_version}


STUB_HEADER = b"\x00\x00\x00\x14ftypisom\x00\x00\x02\x00isom"  # a bare MP4 ftyp box, so file sniffing sees video


def stub_probe(path: Path) -> dict:
    """L0 measurements for SimulatedGenerator(render=False) output: a perfect clip."""
    data = json.loads(path.read_bytes()[len(STUB_HEADER):])
    return {"decodable": True, "width": data["width"], "height": data["height"], "fps": float(data["fps"]),
            "duration_s": round(data["frames"] / data["fps"], 3), "has_audio": True, "black_s": 0.0,
            "black_ranges": [], "freeze_ranges": [], "longest_freeze_s": 0.0, "silence_s": 0.0,
            "max_volume_db": -6.0, "mean_volume_db": -20.0, "decode_errors": 0}


class HttpVideoBackend:
    """Submit / poll / download client for an async video job API (OpenAI-style /v1/videos by default)."""

    FIELDS = {"prompt": "prompt", "negative_prompt": "negative_prompt", "size": "size", "frames": "num_frames",
              "fps": "fps", "seed": "seed", "first_frame": "image", "last_frame": "last_image",
              "references": "references"}
    DONE, FAILED = {"completed", "succeeded", "success"}, {"failed", "error", "cancelled", "canceled"}

    def __init__(self, base_url: str, model: str = "", submit_path: str = "/v1/videos",
                 status_path: str = "/v1/videos/{id}", content_path: str = "/v1/videos/{id}/content",
                 fields: dict | None = None, media: str = "path", extra: dict | None = None, poll_s: float = 2.0,
                 timeout_s: float = 3600, client: httpx.Client | None = None):
        self.client = client or httpx.Client(base_url=base_url, timeout=120)
        self.model, self.submit_path, self.status_path, self.content_path = model, submit_path, status_path, content_path
        self.fields, self.media, self.extra = {**self.FIELDS, **(fields or {})}, media, dict(extra or {})
        self.poll_s, self.timeout_s = poll_s, timeout_s

    def _media(self, path: Path) -> str:
        if self.media == "data_uri":
            mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            return f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode()}"
        return str(path)  # engines mount the shared store at the same path

    def build_request(self, payload: dict, inputs: dict[str, Path]) -> dict:
        f = self.fields
        body = {f["prompt"]: payload["prompt"], f["size"]: f"{payload['width']}x{payload['height']}",
                f["frames"]: payload["frames"], f["fps"]: payload["fps"], f["seed"]: payload["seed"], **self.extra}
        if self.model:
            body["model"] = self.model
        if payload.get("negative_prompt"):
            body[f["negative_prompt"]] = payload["negative_prompt"]
        for name in ("first_frame", "last_frame"):
            if name in inputs:
                body[f[name]] = self._media(inputs[name])
        references = [{"type": r["kind"], "token": r["token"], "data": self._media(inputs[r["token"]])}
                      for r in payload.get("references", [])]
        if references:
            body[f["references"]] = references
        return body

    def generate(self, payload: dict, inputs: dict[str, Path], out: Path, lease=None) -> dict:
        try:
            response = self.client.post(self.submit_path, json=self.build_request(payload, inputs))
            response.raise_for_status()
            job = response.json()
            job_id = job.get("id") or job.get("job_id")
            deadline = time.monotonic() + self.timeout_s
            while str(job.get("status", "")).lower() not in self.DONE | self.FAILED:
                _cancelled(lease)
                if time.monotonic() > deadline:
                    raise BackendError("TIMEOUT", f"生成超过 {self.timeout_s:g}s")
                time.sleep(self.poll_s)
                status = self.client.get(self.status_path.format(id=job_id))
                status.raise_for_status()
                job = status.json()
            if str(job.get("status", "")).lower() in self.FAILED:
                message = str(job.get("error") or job)[:1000]
                raise BackendError(classify(message), message)
            with self.client.stream("GET", self.content_path.format(id=job_id)) as stream:
                stream.raise_for_status()
                with out.open("wb") as handle:
                    for chunk in stream.iter_bytes():
                        handle.write(chunk)
        except httpx.HTTPError as exc:
            raise _http_error(exc) from exc
        return {"model_version": str(job.get("model") or self.model), "engine_job": job_id}


# ---- review ----------------------------------------------------------------------------------------

REVIEW_SYSTEM = (
    "You are the continuity supervisor of an AI short-drama production line. You receive the shot brief, "
    "locked reference images and frames sampled from one generated take. Judge only what the frames show. "
    "Report every concrete problem as a finding: a code from the allowed list, severity, the time range in "
    "seconds and a short message plus a fix suggestion written in Simplified Chinese. If the evidence is "
    "insufficient (for example a face is never visible), answer verdict \"uncertain\" instead of guessing. "
    "Never answer \"fail\" without findings. Output JSON only.")


def review_schema() -> dict:
    finding = {"type": "object", "required": ["code", "severity", "message"], "properties": {
        "code": {"type": "string", "enum": taxonomy.REVIEWABLE},
        "severity": {"type": "string", "enum": ["blocker", "major", "minor"]},
        "t_start": {"type": ["number", "null"]}, "t_end": {"type": ["number", "null"]},
        "message": {"type": "string"}, "suggestion": {"type": "string"}}}
    return {"type": "object", "required": ["verdict", "findings"], "properties": {
        "verdict": {"type": "string", "enum": ["pass", "fail", "uncertain"]},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "score": {"type": "number", "minimum": 0, "maximum": 1},
        "findings": {"type": "array", "items": finding}}}


def shot_brief(payload: dict, level: str) -> str:
    shot = payload["shot"]
    lines = [f"Shot {shot['shot_id']} | importance {shot['importance']} | motion {shot['motion']} | "
             f"duration {payload['duration_s']:.2f}s | review level {level}", "Characters:"]
    lines += [f"- {c['name']} ({c['id']}): {c['appearance']} Costume: {c['costume']}" for c in shot["characters"]]
    lines += [f"Scene: {shot['scene']}", f"Scripted content: {shot['description']} {shot['action']}",
              f"Camera: {shot['camera']}", "Constraints: " + " ".join(shot["constraints"]), "Dialogue in order:"]
    lines += [f"- {d['speaker_name']} [{d['language']}]: {d['text']}" for d in shot["dialogue"]] or ["- (none)"]
    if level == "L3":
        lines.append("Frames labelled 'previous shot' end the preceding approved shot: also check cross-shot "
                     "continuity of costume, props, positions and lighting.")
    lines.append("Allowed codes:")
    lines += [f"- {code}: {taxonomy.CODES[code][2]}" for code in taxonomy.REVIEWABLE]
    return "\n".join(lines)


def extract_frames(video: Path, out_dir: Path, fps: float, max_frames: int, height: int,
                   ffmpeg: str = "ffmpeg") -> list[tuple[float, Path]]:
    """Evenly sampled frames plus the final frame (where continuity errors tend to surface)."""
    subprocess.run([ffmpeg, "-v", "error", "-i", str(video), "-vf", f"fps={fps},scale=-2:{height}",
                    "-frames:v", str(max_frames), "-q:v", "4", str(out_dir / "f_%03d.jpg")],
                   check=True, capture_output=True, timeout=300)
    frames = [(index / fps, path) for index, path in enumerate(sorted(out_dir.glob("f_*.jpg")))]
    last = out_dir / "last.jpg"
    subprocess.run([ffmpeg, "-v", "error", "-sseof", "-0.1", "-i", str(video), "-vf", f"scale=-2:{height}",
                    "-frames:v", "1", "-q:v", "4", str(last)], capture_output=True, timeout=120)
    if last.is_file():
        frames.append((-1.0, last))
    return frames


class _OpenAIChat:
    def __init__(self, base_url: str, model: str, client: httpx.Client | None, api_key: str, timeout: float,
                 max_tokens: int):
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self.client = client or httpx.Client(base_url=base_url, timeout=timeout, headers=headers)
        self.model, self.max_tokens = model, max_tokens

    def chat(self, messages: list[dict], schema_name: str, schema: dict, temperature: float = 0.0) -> dict:
        body = {"model": self.model, "messages": messages, "temperature": temperature, "max_tokens": self.max_tokens,
                "response_format": {"type": "json_schema", "json_schema": {"name": schema_name, "schema": schema}}}
        try:
            response = self.client.post("/v1/chat/completions", json=body)
            response.raise_for_status()
            return parse_json(response.json()["choices"][0]["message"]["content"] or "")
        except httpx.HTTPError as exc:
            raise _http_error(exc) from exc
        except (KeyError, IndexError, TypeError) as exc:
            raise BackendError("BAD_OUTPUT", f"响应格式异常：{exc}") from exc


class VlmReviewer(_OpenAIChat):
    def __init__(self, base_url: str, model: str, level: str = "L1", fps: float = 1.0, max_frames: int = 24,
                 height: int = 448, client: httpx.Client | None = None, api_key: str = "", timeout: float = 600,
                 max_tokens: int = 2048, ffmpeg: str = "ffmpeg"):
        super().__init__(base_url, model, client, api_key, timeout, max_tokens)
        self.level, self.fps, self.max_frames, self.height, self.ffmpeg = level, fps, max_frames, height, ffmpeg

    def review(self, payload: dict, video: Path, refs: list[dict], context: dict[str, Path] | None = None) -> dict:
        with tempfile.TemporaryDirectory() as tmp:
            content: list[dict] = [{"type": "text", "text": shot_brief(payload, self.level)}]
            for ref in refs:
                if ref["kind"] == "image":
                    content += [{"type": "text", "text": f"Locked reference: {ref['role']} of {ref['owner']}"},
                                _image_part(ref["path"])]
            previous = (context or {}).get("previous")
            if previous:
                (Path(tmp) / "prev").mkdir()
                for _, path in extract_frames(previous, Path(tmp) / "prev", 1.0, 3, self.height, self.ffmpeg)[-3:]:
                    content += [{"type": "text", "text": "previous shot"}, _image_part(path)]
            frames = extract_frames(video, Path(tmp), self.fps, self.max_frames, self.height, self.ffmpeg)
            for second, path in frames:
                label = "final frame" if second < 0 else f"t={second:.2f}s"
                content += [{"type": "text", "text": label}, _image_part(path)]
            data = self.chat([{"role": "system", "content": REVIEW_SYSTEM}, {"role": "user", "content": content}],
                             "shot_review", review_schema())
        report = ReviewReport.model_validate({
            "level": self.level, "reviewer": self.model, "verdict": data.get("verdict", "uncertain"),
            "confidence": data.get("confidence"), "score": data.get("score"),
            "findings": data.get("findings", []), "metrics": {"frames": len(frames)}})
        return report.model_dump(mode="json")


def normalize_speech(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).lower()
    return "".join(ch for ch in text if unicodedata.category(ch)[0] in "LN")


def char_error_rate(expected: str, heard: str) -> float:
    """Levenshtein distance over letters/digits only; works for Chinese and English lines alike."""
    ref, hyp = normalize_speech(expected), normalize_speech(heard)
    if not ref:
        return 0.0 if not hyp else 1.0
    previous = list(range(len(hyp) + 1))
    for i, r in enumerate(ref, 1):
        current = [i]
        for j, h in enumerate(hyp, 1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (r != h)))
        previous = current
    return previous[-1] / len(ref)


class AsrReviewer:
    """Measures dialogue accuracy; the threshold is applied centrally from the package policy."""

    def __init__(self, base_url: str, model: str, language: str = "", client: httpx.Client | None = None,
                 api_key: str = "", timeout: float = 600, ffmpeg: str = "ffmpeg"):
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self.client = client or httpx.Client(base_url=base_url, timeout=timeout, headers=headers)
        self.model, self.language, self.ffmpeg = model, language, ffmpeg

    def transcribe(self, video: Path) -> str:
        with tempfile.TemporaryDirectory() as tmp:
            wav = Path(tmp) / "audio.wav"
            subprocess.run([self.ffmpeg, "-v", "error", "-i", str(video), "-vn", "-ac", "1", "-ar", "16000", str(wav)],
                           check=True, capture_output=True, timeout=300)
            data = {"model": self.model, "response_format": "json"}
            if self.language:
                data["language"] = self.language
            try:
                with wav.open("rb") as handle:
                    response = self.client.post("/v1/audio/transcriptions", data=data,
                                                files={"file": ("audio.wav", handle, "audio/wav")})
                response.raise_for_status()
                return str(response.json().get("text", ""))
            except httpx.HTTPError as exc:
                raise _http_error(exc) from exc

    def review(self, payload: dict, video: Path, refs: list[dict], context: dict[str, Path] | None = None) -> dict:
        expected = " ".join(line["text"] for line in payload["shot"]["dialogue"])
        heard = self.transcribe(video)
        return ReviewReport(level="L2.asr", reviewer=self.model, verdict="pass",
                            metrics={"cer": round(char_error_rate(expected, heard), 4), "transcript": heard,
                                     "expected": expected}).model_dump(mode="json")


DIRECTOR_SYSTEM = (
    "You are the on-site repair director of an offline AI short-drama line. A generated take failed review. "
    "Propose the cheapest repair that addresses the root cause, using only allowed_actions and only assets "
    "from usable_assets. You must never change the story, the dialogue, the cast or locked character designs. "
    "revise_prompt may touch only editable_prompt_fields; constraints you give are appended. Escalate when the "
    "remaining budget cannot fix the problem. suggested_plan is the rule engine's proposal; improve it only "
    "when you have a concrete reason. Output JSON only, matching the schema.")


class DirectorClient(_OpenAIChat):
    def __init__(self, base_url: str, model: str, client: httpx.Client | None = None, api_key: str = "",
                 timeout: float = 600, max_tokens: int = 2048):
        super().__init__(base_url, model, client, api_key, timeout, max_tokens)

    def plan(self, payload: dict) -> dict:
        brief = json.dumps(payload["brief"], ensure_ascii=False)
        data = self.chat([{"role": "system", "content": DIRECTOR_SYSTEM}, {"role": "user", "content": brief}],
                         "repair_plan", RepairPlan.model_json_schema(), temperature=0.2)
        return RepairPlan.model_validate({**data, "source": "director"}).model_dump(mode="json")


class SimulatedReviewer:
    def __init__(self, level: str, script: Callable[[dict], dict | None] | None = None):
        self.level, self.script = level, script

    def review(self, payload: dict, video: Path, refs: list[dict], context: dict[str, Path] | None = None) -> dict:
        report = {"level": self.level, "reviewer": f"simulated-{self.level}", "verdict": "pass", "score": 0.9,
                  "findings": [], "metrics": {}}
        if self.level == "L2.asr":
            report["metrics"] = {"cer": 0.0, "transcript": " ".join(d["text"] for d in payload["shot"]["dialogue"])}
        if self.level == "L2.identity":
            report["metrics"] = {"identity": {c["id"]: 0.9 for c in payload["shot"]["characters"]}}
        report.update((self.script(payload) if self.script else None) or {})
        return ReviewReport.model_validate(report).model_dump(mode="json")


class SimulatedDirector:
    def __init__(self, script: Callable[[dict], dict] | None = None):
        self.script = script

    def plan(self, payload: dict) -> dict:
        return self.script(payload) if self.script else payload["brief"]["suggested_plan"]


def _options(config: BackendConfig, level: str | None = None) -> dict:
    options = {k: v for k, v in config.options.items() if k != "per_level"}
    options.update((config.options.get("per_level") or {}).get(level or "", {}))
    if "api_key" in options:
        options["api_key"] = secret(options["api_key"])
    return options


def build_generator(config: BackendConfig, base_url: str):
    if config.kind == "simulated":
        return SimulatedGenerator(**_options(config))
    if config.kind == "http-video":
        return HttpVideoBackend(base_url, **_options(config))
    raise ValueError(f"{config.kind} 不能用于生成池")


def build_reviewer(config: BackendConfig, base_url: str, level: str):
    if config.kind == "simulated-review":
        return SimulatedReviewer(level)
    if config.kind == "openai-chat":
        return VlmReviewer(base_url, level=level, **_options(config, level))
    if config.kind == "openai-asr" and level == "L2.asr":
        return AsrReviewer(base_url, **_options(config, level))
    raise ValueError(f"{config.kind} 不能用于 {level} 审核")


def build_director(config: BackendConfig, base_url: str):
    if config.kind == "simulated-review":
        return SimulatedDirector()
    if config.kind == "openai-chat":
        return DirectorClient(base_url, **{k: v for k, v in _options(config).items()
                                           if k in ("model", "api_key", "timeout", "max_tokens")})
    raise ValueError(f"{config.kind} 不能用于 director")
