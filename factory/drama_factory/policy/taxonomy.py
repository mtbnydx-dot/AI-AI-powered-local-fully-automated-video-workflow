"""Review finding codes. Category picks the repair strategy; severity decides pass or fail."""
from typing import Literal

Severity = Literal["blocker", "major", "minor"]

# code -> (category, default severity, meaning given to VLM reviewers)
CODES: dict[str, tuple[str, Severity, str]] = {
    # L0: measured on the node with ffprobe/ffmpeg
    "CORRUPT_FILE": ("technical", "blocker", "file cannot be fully decoded"),
    "WRONG_RESOLUTION": ("config", "blocker", "output size differs from the request"),
    "WRONG_FPS": ("config", "blocker", "output frame rate differs from 24 fps"),
    "DURATION_MISMATCH": ("technical", "major", "duration differs from the request"),
    "BLACK_FRAMES": ("technical", "major", "black frames"),
    "FROZEN_FRAMES": ("technical", "major", "frozen picture in a moving shot"),
    "AUDIO_MISSING": ("technical", "major", "no audio track"),
    "AUDIO_CLIPPING": ("technical", "minor", "clipped audio"),
    # identity and continuity
    "IDENTITY_DRIFT": ("identity", "blocker", "a character's face or identity changes or differs from the reference"),
    "WRONG_CHARACTER": ("identity", "blocker", "the wrong person appears or a scripted character is missing"),
    "EXTRA_CHARACTER": ("identity", "major", "an unscripted person appears in focus"),
    "COSTUME_DRIFT": ("identity", "major", "clothing, hairstyle or accessories change or differ from the bible"),
    "PROP_DISCONTINUITY": ("continuity", "major", "a prop appears, vanishes, jumps hands or changes"),
    "SCENE_MISMATCH": ("continuity", "major", "location, lighting or time of day differs from the scene"),
    # motion
    "ACTION_INCOMPLETE": ("motion", "major", "the scripted action is missing or unfinished"),
    "ANATOMY_ERROR": ("motion", "blocker", "deformed hands, limbs or faces"),
    "PHYSICS_ERROR": ("motion", "major", "physically impossible motion or object behaviour"),
    "CAMERA_MISMATCH": ("motion", "minor", "camera framing or movement differs from the shot description"),
    # dialogue and sound
    "DIALOGUE_MISMATCH": ("dialogue", "blocker", "spoken words differ from the scripted lines"),
    "DIALOGUE_MISSING": ("dialogue", "blocker", "a scripted line is not spoken"),
    "VOICE_DRIFT": ("dialogue", "major", "a character's voice changes"),
    "LIPSYNC_OFFSET": ("dialogue", "major", "mouth movement does not match speech, or the wrong person speaks"),
    "CROSSTALK": ("dialogue", "major", "speakers overlap so a line is unintelligible"),
    # artifacts and story
    "TEXT_ARTIFACT": ("artifact", "major", "readable text, logo, subtitle or watermark"),
    "VISUAL_ARTIFACT": ("artifact", "major", "melting, flicker, smear or other generation artifact"),
    "STORY_DEVIATION": ("story", "blocker", "events contradict the scripted beat"),
}
REVIEWABLE = [code for code, (category, _, _) in CODES.items() if category not in ("technical", "config")]
INFRA_CODES = frozenset({"OOM", "TIMEOUT", "WORKER_LOST", "ENGINE_ERROR", "IO_ERROR", "BAD_OUTPUT", "CANCELLED"})


def category(code: str) -> str:
    return CODES.get(code, ("artifact", "major", ""))[0]


def default_severity(code: str) -> Severity:
    return CODES.get(code, ("artifact", "major", ""))[1]
