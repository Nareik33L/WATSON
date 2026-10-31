"""Package-data paths for templates, fonts, and bundled scenarios."""

from __future__ import annotations

import os
from contextlib import ExitStack
from importlib.resources import as_file, files
from pathlib import Path

_STACK = ExitStack()
_DIRS: dict[str, Path] = {}


def _package_dir(name: str) -> Path:
    """Resolve a data directory shipped inside watson_fixturegen.

    Uses importlib.resources so the same path works from the source tree and
    from an installed wheel (no repo-relative lookups).
    """
    cached = _DIRS.get(name)
    if cached is not None:
        return cached
    resource = files("watson_fixturegen").joinpath(name)
    path = Path(_STACK.enter_context(as_file(resource)))
    if not path.is_dir():
        raise FileNotFoundError(f"watson_fixturegen package data {name!r} missing at {path}")
    _DIRS[name] = path
    return path


def templates_dir() -> Path:
    return _package_dir("templates")


def fonts_dir() -> Path:
    return _package_dir("fonts")


def scenarios_dir() -> Path:
    return _package_dir("scenarios")


def meetings_scripts_dir() -> Path:
    """YAML meeting scripts live next to the package (not inside the wheel)."""
    override = os.environ.get("WATSON_MEETINGS_SCRIPTS_DIR")
    if override:
        return Path(override).resolve()
    sibling = Path(__file__).resolve().parent.parent / "meetings"
    if sibling.is_dir() and any(sibling.glob("*.yaml")):
        return sibling
    starts = [Path.cwd().resolve()]
    for start in starts:
        for parent in (start, *start.parents):
            candidate = parent / "tools" / "fixture_gen" / "meetings"
            if candidate.is_dir() and any(candidate.glob("*.yaml")):
                return candidate
    raise FileNotFoundError(
        "meeting scripts directory not found. Pass a script path or run from the checkout."
    )
