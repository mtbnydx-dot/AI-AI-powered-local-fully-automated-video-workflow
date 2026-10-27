"""MiniMax H3 conventions: 24 FPS, 17n+5 frame counts, 768p base canvas, prompt sections and tags."""
import re

FPS = 24
FRAME_STEP, FRAME_OFFSET = 17, 5
# 768p base canvases (multiples of 32). Delivery 1080x1920 is post-production upscaling, not H3 output.
CANVAS = {"16:9": (1344, 768), "9:16": (768, 1344), "1:1": (768, 768)}
REF_LIMITS = {"image": 9, "video": 3, "audio": 3, "total": 12}
TOKEN_KIND = {"image": "Picture", "video": "Video", "audio": "Audio"}
REF_TOKEN = re.compile(r"<(Picture|Video|Audio) (\d+)>")
DIALOGUE_TAG = re.compile(r"<d>\s*\[([^\]]+)\]\s*(.*?)\s*</d>", re.S)


def align_frames(seconds: float) -> int:
    """Nearest valid H3 frame count (17n+5) for a duration, never below one step."""
    n = max(1, round((seconds * FPS - FRAME_OFFSET) / FRAME_STEP))
    return n * FRAME_STEP + FRAME_OFFSET


def valid_frames(frames: int) -> bool:
    return frames >= FRAME_STEP + FRAME_OFFSET and (frames - FRAME_OFFSET) % FRAME_STEP == 0


def normalize(text: str) -> str:
    return " ".join(text.split())


def dialogue_tags(text: str) -> list[tuple[str, str]]:
    return [(language.strip(), normalize(line)) for language, line in DIALOGUE_TAG.findall(text)]


def ref_tokens(text: str) -> set[tuple[str, int]]:
    return {(kind, int(number)) for kind, number in REF_TOKEN.findall(text)}


def ref_slots(kinds: list[str]) -> list[tuple[str, int]]:
    """Token per reference in list order; each media type is numbered independently."""
    counters: dict[str, int] = {}
    slots = []
    for kind in kinds:
        counters[kind] = counters.get(kind, 0) + 1
        slots.append((TOKEN_KIND[kind], counters[kind]))
    return slots


def prompt_texts(prompt) -> list[str]:
    return [prompt.description, prompt.action, prompt.camera, *prompt.constraints, prompt.soundscape, prompt.music]


def render_prompt(prompt) -> str:
    """Render a structured ShotPrompt into the sectioned text the existing H3 projects use."""
    parts = (prompt.description, prompt.action, prompt.camera, *prompt.constraints)
    sections = ["integrated_multimodal_description: " + " ".join(p.strip() for p in parts if p.strip())]
    if prompt.soundscape.strip():
        sections.append(f"overall_soundscape: {prompt.soundscape.strip()}")
    if prompt.music.strip():
        sections.append(f"non_diegetic_music: {prompt.music.strip()}")
    return "\n\n".join(sections) + "\n"
