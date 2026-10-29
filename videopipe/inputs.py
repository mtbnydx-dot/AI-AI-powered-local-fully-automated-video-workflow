import json
from pathlib import Path

import yaml

from .models import PipelineConfig, ScriptPackage


def load_package(path: Path) -> ScriptPackage:
    path = path.resolve()
    package = ScriptPackage.model_validate(json.loads(path.read_text(encoding="utf-8")))
    ids = [shot.id for shot in package.shots]
    if len(ids) != len(set(ids)):
        raise ValueError("Shot IDs must be unique")
    package.script = _file(path.parent, package.script)
    for shot in package.shots:
        shot.reference_images = [_file(path.parent, ref) for ref in shot.reference_images]
    return package


def load_config(path: Path) -> PipelineConfig:
    path = path.resolve()
    config = PipelineConfig.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
    for name in ("workflow", "upscale_workflow"):
        value = getattr(config.comfyui, name)
        if value is not None:
            setattr(config.comfyui, name, _file(path.parent, value))
    if config.generator == "comfyui" and config.workers > len(config.comfyui.urls):
        raise ValueError("workers must not exceed the number of ComfyUI service URLs")
    return config


def validate_plan(package: ScriptPackage, config: PipelineConfig) -> None:
    classes = []
    for path in (config.comfyui.workflow, config.comfyui.upscale_workflow):
        if path is not None:
            graph = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(graph, dict) or not graph or any(
                not isinstance(node, dict) or not isinstance(node.get("class_type"), str)
                or not isinstance(node.get("inputs"), dict) for node in graph.values()
            ):
                raise ValueError(f"Workflow must use ComfyUI API format (class_type and inputs per node): {path}")
            if path == config.comfyui.workflow:
                classes = [node["class_type"] for node in graph.values()]
    if config.generator != "comfyui":
        return
    workflow = config.comfyui.workflow
    native_h3 = workflow is None or any(name.startswith("MiniMaxH3") for name in classes)
    if native_h3:
        for shot in package.shots:
            values = {**shot.model_dump(), **config.comfyui.parameters, **shot.parameters}
            if values["fps"] != 24 or values["width"] % 32 or values["height"] % 32:
                raise ValueError(f"H3 shot {shot.id} requires 24 fps and dimensions divisible by 32")


def _file(base: Path, value: Path) -> Path:
    result = (base / value).resolve()
    if not result.is_file():
        raise ValueError(f"Input file does not exist: {result}")
    return result
