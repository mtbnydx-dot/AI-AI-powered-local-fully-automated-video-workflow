"""TakeSpec: the exact, reproducible generation recipe for one round of a shot."""
import hashlib

from pydantic import BaseModel

from ..protocol import h3
from ..protocol.manifest import Mode, Shot, ShotPrompt, frames_of


class TakeSpec(BaseModel):
    route: str
    mode: Mode
    prompt: ShotPrompt
    first_frame: str | None = None
    last_frame: str | None = None
    references: list[str] = []
    frames: int
    width: int
    height: int

    @classmethod
    def initial(cls, shot: Shot, route: str) -> "TakeSpec":
        width, height = h3.CANVAS[shot.aspect_ratio]
        return cls(route=route, mode=shot.mode, prompt=shot.prompt.model_copy(deep=True),
                   first_frame=shot.first_frame, last_frame=shot.last_frame, references=list(shot.references),
                   frames=frames_of(shot), width=width, height=height)


def seed_for(package_id: str, shot_id: str, round_no: int, index: int) -> int:
    """Deterministic per (shot, round, candidate) so any take can be regenerated bit-for-bit."""
    digest = hashlib.sha256(f"{package_id}/{shot_id}/{round_no}/{index}".encode()).digest()
    return int.from_bytes(digest[:4], "big") & 0x7FFFFFFF
