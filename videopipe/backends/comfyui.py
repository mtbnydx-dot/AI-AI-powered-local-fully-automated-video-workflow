import json
import math
import mimetypes
import re
import time
from pathlib import Path
from typing import Any

import httpx

from videopipe.models import ComfyConfig, Shot


WORKFLOWS = Path(__file__).resolve().parents[1] / "workflows"
DEFAULT_PARAMETERS = {
    "diffusion_model": "minimax_h3_fl2va_pruned_int8_convrot.safetensors",
    "text_encoder": "qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors",
    "video_vae": "minimax_h3_video_vae_int8_convrot.safetensors",
    "audio_vae": "minimax_h3_audio_vae_fp32.safetensors",
    "steps": 20,
    "sampler": "res_multistep",
    "scheduler": "simple",
    "upscale_model": "RealESRGAN_x2plus.pth",
}


class ComfyGenerator:
    def __init__(self, config: ComfyConfig, url: str | None = None):
        self.config = config
        self.url = (url or config.urls[0]).rstrip("/")

    def generate(self, shot: Shot, output_dir: Path, *, attempt: int, feedback: str = "") -> Path:
        output_dir.mkdir(parents=True, exist_ok=True)
        if (output_dir / "prompt.json").is_file():
            return self._resume(output_dir)
        first_frames = ([asset.path for asset in shot.asset_bindings if asset.role == "first_frame"]
                        if shot.asset_bindings else shot.reference_images[:1])
        workflow = self.config.workflow or WORKFLOWS / ("h3_i2v.json" if first_frames else "h3_t2v.json")
        values = {
            **DEFAULT_PARAMETERS,
            "prompt": shot.prompt,
            "negative_prompt": shot.negative_prompt,
            "width": shot.width,
            "height": shot.height,
            "fps": shot.fps,
            "frames": self._frames(shot.duration, shot.fps),
            "seed": shot.seed,
            "duration": shot.duration,
            "attempt": attempt,
            **self.config.parameters,
            **shot.parameters,
        }
        # The input module owns the approved text. Review feedback never changes generation input.
        values["h3_prompt"] = shot.prompt
        values["filename_prefix"] = output_prefix(output_dir)
        with httpx.Client(base_url=self.url, timeout=self.config.timeout, trust_env=False) as client:
            references = [self._upload(client, image) for image in first_frames]
            values["reference_images"] = references
            if references:
                values["reference_image"] = references[0]
                values.update({f"reference_image_{i}": name for i, name in enumerate(references)})
            return self._execute(client, workflow, values, output_dir)

    def upscale(self, video: Path, output_dir: Path, *, factor: int) -> Path:
        output_dir.mkdir(parents=True, exist_ok=True)
        if (output_dir / "prompt.json").is_file():
            return self._resume(output_dir)
        workflow = self.config.upscale_workflow or WORKFLOWS / "upscale.json"
        with httpx.Client(base_url=self.url, timeout=self.config.timeout, trust_env=False) as client:
            values = {
                **DEFAULT_PARAMETERS,
                **self.config.parameters,
                "reference_video": self._upload(client, video),
                "factor": factor,
                "filename_prefix": output_prefix(output_dir),
            }
            return self._execute(client, workflow, values, output_dir)

    def _frames(self, duration: float, fps: int) -> int:
        requested = max(self.config.frame_offset, math.ceil(duration * fps))
        return requested + (self.config.frame_offset - requested) % self.config.frame_multiple

    def _upload(self, client: httpx.Client, path: Path) -> str:
        with path.open("rb") as media:
            response = client.post("/upload/image", data={"type": "input"}, files={
                "image": (path.name, media, mimetypes.guess_type(path.name)[0] or "application/octet-stream"),
            })
        uploaded = response_json(response)
        return "/".join(part for part in (uploaded.get("subfolder", ""), uploaded["name"]) if part)

    def _execute(self, client: httpx.Client, workflow: Path, values: dict, output_dir: Path) -> Path:
        graph = render_workflow(json.loads(workflow.read_text()), values)
        request = {"prompt": graph}
        save_json(output_dir / "parameters.json", values)
        save_json(output_dir / "workflow.json", graph)
        save_json(output_dir / "request.json", {"url": self.url, "workflow": str(workflow), **request})
        response = client.post("/prompt", json=request)
        save_json(output_dir / "submission.json", {"http_status": response.status_code, "body": response.text})
        submitted = response_json(response)
        if submitted.get("node_errors"):
            raise RuntimeError(f"ComfyUI rejected workflow nodes: {submitted['node_errors']}")
        prompt_id = submitted["prompt_id"]
        save_json(output_dir / "prompt.json", {"prompt_id": prompt_id, "url": self.url})
        return self._download_result(client, prompt_id, output_dir)

    def _resume(self, output_dir: Path) -> Path:
        """Continue the recorded server task; a timeout is not a request to generate another take."""
        submitted = json.loads((output_dir / "prompt.json").read_text(encoding="utf-8"))
        with httpx.Client(base_url=submitted["url"], timeout=self.config.timeout, trust_env=False) as client:
            return self._download_result(client, submitted["prompt_id"], output_dir)

    def _download_result(self, client: httpx.Client, prompt_id: str, output_dir: Path) -> Path:
        history = self._wait(client, prompt_id, output_dir)
        result = video_output(history)
        target = output_dir / ("video" + Path(result["filename"]).suffix)
        with client.stream("GET", "/view", params={
            "filename": result["filename"],
            "subfolder": result.get("subfolder", ""),
            "type": result.get("type", "output"),
        }) as response:
            response.raise_for_status()
            with target.open("wb") as destination:
                for data in response.iter_bytes():
                    destination.write(data)
        return target

    def _wait(self, client: httpx.Client, prompt_id: str, output_dir: Path) -> dict:
        deadline = time.monotonic() + self.config.timeout
        while time.monotonic() < deadline:
            history = response_json(client.get(f"/history/{prompt_id}")).get(prompt_id)
            if history:
                save_json(output_dir / "history.json", history)
                status = history.get("status", {})
                if status.get("status_str") == "error":
                    raise RuntimeError(f"ComfyUI execution failed ({prompt_id}): {status.get('messages', [])}")
                if status.get("completed"):
                    return history
            time.sleep(self.config.poll_interval)
        raise TimeoutError(f"ComfyUI task {prompt_id} exceeded {self.config.timeout}s; server task is left running. See {output_dir / 'prompt.json'}")


def output_prefix(output_dir: Path) -> str:
    """Keep run / shot / take context in ComfyUI filenames without exposing an absolute path."""
    context = [re.sub(r"[^A-Za-z0-9_.-]", "_", part) for part in output_dir.resolve().parts[-5:]]
    return "/".join(["videopipe", *context, "video"])


def render_workflow(template: Any, values: dict[str, Any]) -> Any:
    if isinstance(template, dict):
        return {key: render_workflow(value, values) for key, value in template.items()}
    if isinstance(template, list):
        return [render_workflow(value, values) for value in template]
    if isinstance(template, str) and (match := re.fullmatch(r"\{\{([A-Za-z0-9_]+)\}\}", template)):
        key = match.group(1)
        if key not in values:
            raise ValueError(f"Missing workflow parameter: {key}")
        return values[key]
    return template


def response_json(response: httpx.Response) -> dict:
    if response.is_error:
        raise RuntimeError(f"ComfyUI HTTP {response.status_code}: {response.text}")
    return response.json()


def video_output(history: dict) -> dict:
    for node_output in history.get("outputs", {}).values():
        for field in ("images", "videos", "gifs"):
            for item in node_output.get(field, []):
                if Path(item["filename"]).suffix.lower() in {".mp4", ".webm", ".mkv", ".mov"} and item.get("type", "output") == "output":
                    return item
    raise RuntimeError("ComfyUI completed without a saved video; workflow must include SaveVideo")


def save_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
