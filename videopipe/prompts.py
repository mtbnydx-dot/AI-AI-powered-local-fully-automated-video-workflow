"""Read the project-level prompt directory in source and installed layouts."""

from pathlib import Path


PROMPT_DIR = Path(__file__).resolve().parent.parent / "prompt"


def load_prompt(name: str, **values: object) -> str:
    template = (PROMPT_DIR / name).read_text(encoding="utf-8").strip()
    return template.format(**values) if values else template
