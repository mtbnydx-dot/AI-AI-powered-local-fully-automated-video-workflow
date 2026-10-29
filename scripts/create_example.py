"""Rebuild the original product reference images and their complete test script package."""

import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter


def paint_reference(path: Path, framing: str) -> None:
    image = Image.new("RGB", (1440, 800), "#ddcdb8")
    draw = ImageDraw.Draw(image)
    draw.polygon([(0, 0), (380, 0), (1100, 540), (490, 540)], fill="#f8eed6")
    draw.rectangle((0, 520, 1440, 800), fill="#aa7955")
    for y in range(540, 800, 44):
        draw.line((0, y, 1440, y + 10), fill="#b88660", width=4)
    draw.polygon([(70, 580), (500, 540), (670, 800), (0, 800)], fill="#445c65")
    for x in range(100, 480, 13):
        draw.line((x, 580, x + 160, 800), fill="#4a636b", width=2)
    shadow = Image.new("RGBA", image.size, (0, 0, 0, 0))
    ImageDraw.Draw(shadow).ellipse((440, 620, 1140, 738), fill=(55, 31, 12, 100))
    image = Image.alpha_composite(image.convert("RGBA"), shadow.filter(ImageFilter.GaussianBlur(22)))
    draw = ImageDraw.Draw(image)
    draw.ellipse((883, 362, 1105, 600), fill="#a7431e")
    draw.ellipse((931, 405, 1058, 550), fill="#ddcdb8")
    draw.rounded_rectangle((510, 325, 950, 680), radius=72, fill="#c65c2c")
    draw.rounded_rectangle((535, 335, 720, 649), radius=40, fill="#d66c32")
    draw.ellipse((510, 280, 950, 387), fill="#ef9b55")
    draw.ellipse((535, 297, 925, 368), fill="#803e26")
    draw.arc((535, 298, 925, 369), 180, 340, fill="#f7ba75", width=5)
    draw.line((552, 397, 552, 581), fill="#e78a4c", width=8)
    draw.arc((542, 583, 924, 687), 0, 180, fill="#a44925", width=7)
    if framing == "medium":
        image = image.crop((280, 160, 1300, 727))
    elif framing == "detail":
        image = image.crop((445, 225, 1129, 605))
    image.convert("RGB").resize((864, 480), Image.Resampling.LANCZOS).save(path)


def main() -> None:
    directory = Path(__file__).resolve().parents[1] / "examples" / "product"
    references = directory / "references"
    references.mkdir(parents=True, exist_ok=True)
    for name in ("wide", "medium", "detail"):
        paint_reference(references / f"{name}.png", name)
    descriptions = [
        ("wide", "A wide product establishing shot of one orange ceramic mug on a warm wooden table, "
         "handle on the right, navy linen on the left, soft afternoon sunlight from the upper left. "
         "Slow smooth camera push-in. The mug stays stationary. Warm minimalist commercial style."),
        ("medium", "Medium close-up of the same orange ceramic mug, handle on the right, on the same "
         "wooden table under warm upper-left sunlight. Slow smooth camera push-in emphasizing the "
         "curved ceramic body and glaze. Preserve the reference shape and orange color."),
        ("detail", "Close detail of the same orange ceramic mug's elliptical rim and right-side handle. "
         "Warm upper-left sunlight glides across the glazed ceramic surface as the camera slowly pushes in. "
         "The mug remains rigid and stationary. Clean premium product finish."),
    ]
    shots = []
    for index, (name, prompt) in enumerate(descriptions, 1):
        shots.append({
            "id": f"shot-{index:02d}", "prompt": prompt,
            "negative_prompt": "warped mug, duplicate handles, extra objects, text, watermark, flicker, "
                               "camera shake, changing color, distorted geometry",
            "reference_images": [f"references/{name}.png"], "duration": 2,
            "width": 864, "height": 480, "fps": 24, "seed": 4100 + index,
            "acceptance": ["One orange mug with a single right-side handle remains consistent with the reference.",
                           "Warm light and the wooden tabletop remain consistent across shots.",
                           "Camera motion is gentle; no severe flicker, geometry deformation or unintended text."],
        })
    package = {"title": "Amber Cup — A Quiet Afternoon", "script": "script.md", "shots": shots}
    (directory / "package.json").write_text(json.dumps(package, ensure_ascii=False, indent=2) + "\n")
    (directory / "script.md").write_text(
        "# Amber Cup — A Quiet Afternoon\n\n"
        "A six-second, three-shot product film introducing a single orange ceramic mug. "
        "The mug has a rounded body and one handle on its right. "
        "Warm upper-left sunlight, a wooden table and navy linen establish a consistent setting.\n\n"
        "1. **0–2 s, wide:** Establish the mug and quiet tabletop with a gentle push-in.\n"
        "2. **2–4 s, medium:** Move closer to show the orange glaze and curved body.\n"
        "3. **4–6 s, detail:** Finish on the elliptical rim and right-side handle.\n\n"
        "Use straight cuts. No dialogue, music, subtitles or on-screen text are required. "
        "Review product identity, shape, color, lighting and temporal stability.\n"
    )
    (directory / "README.md").write_text(
        "# Product test package\n\n"
        "Three 2-second shots at 864×480 / 24 fps, with complete prompts, negative prompts, "
        "acceptance criteria and reference images. Paths are relative to package.json.\n\n"
        "The CPU backend makes camera-motion previews from these images; it does not perform AI inference. "
        "Use this same package with a configured ComfyUI GPU backend to test real generation.\n\n"
        "All reference images are original procedural drawings created by this repository's "
        "scripts/create_example.py, with no downloaded photographs, fonts or third-party artwork. "
        "The images and script package are dedicated to the public domain under "
        "[CC0 1.0](https://creativecommons.org/publicdomain/zero/1.0/).\n\n"
        "Rebuild with `python scripts/create_example.py`; edit that source rather than generated files.\n"
    )
    print(directory)


if __name__ == "__main__":
    main()
