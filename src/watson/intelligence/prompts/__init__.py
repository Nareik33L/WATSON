"""Versioned prompt files: ``intelligence/prompts/<task>/<version>.md``."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


class PromptError(ValueError):
    """Prompt file is missing or unreadable."""


@dataclass(frozen=True)
class Prompt:
    task: str
    version: str
    text: str

    @property
    def prompt_version(self) -> str:
        return f"{self.task}/{self.version}"


_ROOT = Path(__file__).resolve().parent


def load_prompt(task: str, version: str = "v1") -> Prompt:
    """Load ``prompts/<task>/<version>.md`` and return it with ``prompt_version``."""
    if not task.isidentifier() or "/" in version or "\\" in version or ".." in version:
        raise PromptError(f"invalid prompt identity {task!r}/{version!r}")
    path = _ROOT / task / f"{version}.md"
    if not path.is_file():
        raise PromptError(f"prompt not found: {task}/{version}")
    text = path.read_text(encoding="utf-8")
    if not text.strip():
        raise PromptError(f"prompt is empty: {task}/{version}")
    return Prompt(task=task, version=version, text=text)
