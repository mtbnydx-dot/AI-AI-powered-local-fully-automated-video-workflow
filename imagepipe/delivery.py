"""Write generated images into the package's existing asset paths."""

from __future__ import annotations

from pathlib import Path

from PIL import Image


def deliver_image(original: Path, target: Path, width: int, height: int) -> dict:
    with Image.open(original) as source:
        source_size = source.size
        image = source.convert("RGB")
    source_width, source_height = source_size
    scale = max(width / source_width, height / source_height)
    crop_width, crop_height = width / scale, height / scale
    left = (source_width - crop_width) / 2
    top = (source_height - crop_height) / 2
    crop_box = (left, top, left + crop_width, top + crop_height)
    resized = source_size != (width, height)
    if resized:
        image = image.resize((width, height), Image.Resampling.LANCZOS, box=crop_box)
    target.parent.mkdir(parents=True, exist_ok=True)
    image.save(target, format="PNG")
    return {
        "source_size": list(source_size),
        "output_size": [width, height],
        "crop_box": [round(value, 3) for value in crop_box],
        "resized": resized,
        "mode": "RGB",
    }
