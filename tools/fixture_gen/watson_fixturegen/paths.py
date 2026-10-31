"""Package-relative paths for templates and bundled scenarios."""

from __future__ import annotations

from pathlib import Path


def package_dir() -> Path:
    return Path(__file__).resolve().parent


def templates_dir() -> Path:
    return package_dir() / "templates"


def fonts_dir() -> Path:
    return tools_root() / "fonts"


def tools_root() -> Path:
    return package_dir().parent


def scenarios_dir() -> Path:
    return tools_root() / "scenarios"
