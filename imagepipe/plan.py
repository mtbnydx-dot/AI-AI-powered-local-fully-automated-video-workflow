"""Compile episode or season image slots into one dependency-driven plan."""

import json
from graphlib import TopologicalSorter
from pathlib import Path

from videopipe.prompts import load_prompt

from .models import CopyTask, ImageConfig, ImagePlan, ImageTask


def compile_plan(package_dir: Path, config: ImageConfig) -> ImagePlan:
    root = Path(package_dir).resolve()
    selected, catalogue, copies, style = _load_inputs(root)
    references, masters = _dependencies(selected, catalogue, config.continuity)
    tasks = []
    for index, asset_id in enumerate(selected):
        slot = catalogue[asset_id]
        reference_ids = references[asset_id]
        template, mode = _generation_kind(slot, masters, reference_ids)
        slot_style = slot.get("style_en", style).strip()
        prompt = load_prompt(
            f"imagepipe/{template}.txt",
            style=slot_style,
            description=_description(slot, slot_style),
            reference_guide=_reference_guide(reference_ids, catalogue, slot_style),
            width=config.generation.width,
            height=config.generation.height,
        )
        tasks.append(ImageTask(
            asset_id=asset_id,
            mode=mode,
            prompt=prompt,
            reference_ids=reference_ids,
            target_path=slot["_target"],
            seed=config.generation.seed_base + index,
        ))
    return ImagePlan(package_root=str(root), tasks=tasks, copies=copies)


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_inputs(root: Path) -> tuple[list[str], dict[str, dict], list[CopyTask], str]:
    manifest = _read_json(root / "image_slots.json")
    season_path = next((p / "season.json" for p in (root, *root.parents)
                        if (p / "season.json").exists()), None)
    style = _read_json(season_path).get("style_en", "") if season_path else ""
    catalogue = {}
    selected = []
    copies = []

    if "shared_assets" in manifest:
        slots = manifest["shared_assets"] + manifest.get("first_frames", [])
        for slot in slots:
            asset_id = slot["asset_id"]
            if asset_id in catalogue:
                raise ValueError(f"Duplicate image asset ID: {asset_id}")
            catalogue[asset_id] = {**slot, "_target": str((root / slot["path"]).resolve())}
            selected.append(asset_id)
        for path in sorted(root.rglob("image_slots.json")):
            if path.parent == root:
                continue
            for slot in _read_json(path).get("slots", []):
                if "source_path" in slot:
                    copies.append(CopyTask(
                        asset_id=slot.get("source_asset_id", slot["asset_id"]),
                        source_path=str((path.parent / slot["source_path"]).resolve()),
                        target_path=str((path.parent / slot["path"]).resolve()),
                    ))
    else:
        # A standalone episode can still depend on the season's canonical mother images.
        for parent in root.parents:
            path = parent / "image_slots.json"
            if path.exists():
                parent_manifest = _read_json(path)
                shared = parent_manifest.get("shared_assets", [])
                if shared:
                    catalogue.update({slot["asset_id"]: {
                        **slot, "_target": str((parent / slot["path"]).resolve()),
                    } for slot in shared + parent_manifest.get("first_frames", [])})
                    break
        for slot in manifest["slots"]:
            asset_id = slot["asset_id"]
            if asset_id in selected:
                raise ValueError(f"Duplicate image asset ID: {asset_id}")
            selected.append(asset_id)
            if "source_path" in slot:
                source_path = str((root / slot["source_path"]).resolve())
                source = catalogue.get(slot.get("source_asset_id", asset_id), slot)
                catalogue[asset_id] = {**source, "asset_id": asset_id, "_target": source_path}
                copies.append(CopyTask(
                    asset_id=asset_id,
                    source_path=source_path,
                    target_path=str((root / slot["path"]).resolve()),
                ))
            else:
                catalogue[asset_id] = {**slot, "_target": str((root / slot["path"]).resolve())}
    return selected, catalogue, copies, style


def _dependencies(selected: list[str], catalogue: dict[str, dict], continuity: dict
                  ) -> tuple[dict[str, list[str]], dict[str, str]]:
    masters = {}
    for asset_id, slot in catalogue.items():
        character_id = slot.get("character_id")
        if character_id:
            masters.setdefault(character_id, asset_id)
    anchors = _frame_anchors(catalogue, continuity.get("sets", []))
    additional = continuity.get("additional_reference_assets", {})
    references = {}
    # Appending dependencies here also brings missing season masters into an episode run.
    for asset_id in selected:
        slot = catalogue[asset_id]
        refs = list(slot.get("reference_asset_ids", []))
        character_id = slot.get("character_id")
        if character_id and masters[character_id] != asset_id:
            refs.insert(0, masters[character_id])
        refs.extend(additional.get(asset_id, []))
        if asset_id in anchors:
            refs.append(anchors[asset_id])
        refs = list(dict.fromkeys(refs))
        if len(refs) > 10:
            raise ValueError(f"{asset_id} needs {len(refs)} reference images; the service accepts at most 10")
        for reference_id in refs:
            if reference_id not in catalogue:
                raise ValueError(f"{asset_id} references missing asset {reference_id}")
            if reference_id not in selected:
                selected.append(reference_id)
        references[asset_id] = refs
    TopologicalSorter(references).prepare()
    return references, masters


def _frame_anchors(catalogue: dict[str, dict], sets: list[dict]) -> dict[str, str]:
    if not sets:
        return {}
    aliases: dict[str, list[str]] = {}
    for asset_id, slot in catalogue.items():
        if slot.get("purpose") != "first_frame":
            continue
        labels = {asset_id, slot["shot_id"]}
        if slot.get("episode_id"):
            labels.add(f"{slot['episode_id']}/{slot['shot_id']}")
        for label in labels:
            aliases.setdefault(label, []).append(asset_id)
    anchors = {}
    for group in sets:
        shots = group.get("shot_ids", group.get("shots", []))
        anchor_label = group.get("anchor_shot", shots[0] if shots else "")
        resolved = []
        for label in [anchor_label, *shots]:
            matches = aliases.get(label, [])
            if len(matches) != 1:
                raise ValueError(f"Set {group.get('set_id', '')}: shot {label!r} is missing or ambiguous")
            resolved.append(matches[0])
        anchor = resolved[0]
        for member in resolved[1:]:
            if member != anchor:
                anchors[member] = anchor
    return anchors


def _generation_kind(slot: dict, masters: dict[str, str], references: list[str]) -> tuple[str, str]:
    if slot.get("purpose") == "first_frame":
        template = "first_frame"
    elif slot.get("character_id"):
        template = ("character_master" if masters[slot["character_id"]] == slot["asset_id"]
                    else "wardrobe_edit")
    elif slot.get("location_id"):
        template = "location_reference"
    else:
        template = "prop_edit" if references else "prop_reference"
    mode = "text"
    if references:
        mode = "edit" if len(references) == 1 and template in {"wardrobe_edit", "prop_edit"} else "reference"
    return template, mode


def _description(slot: dict, style: str) -> str:
    description = slot["prompt_en"].strip()
    if style and description.startswith(style):
        description = description[len(style):].strip()
    return description


def _reference_guide(references: list[str], catalogue: dict[str, dict], style: str) -> str:
    lines = []
    for index, asset_id in enumerate(references, start=1):
        slot = catalogue[asset_id]
        if slot.get("purpose") == "first_frame":
            role = "environment_anchor"
        elif slot.get("character_id"):
            role = "character_identity_and_wardrobe"
        elif slot.get("location_id"):
            role = "location_continuity_reference"
        else:
            role = "prop_continuity_reference"
        label = "the reference image" if len(references) == 1 else f"<image{index}>"
        fields = [f"asset_id={asset_id}", f"role={role}"]
        fields.extend(f"{key}={slot[key]}" for key in ("character_id", "wardrobe_id", "location_id", "prop_id")
                      if slot.get(key))
        if role != "environment_anchor":
            fields.append(f"description={_description(slot, slot.get('style_en', style))}")
        lines.append(f"{label}: " + "; ".join(fields))
    return "\n".join(lines)
