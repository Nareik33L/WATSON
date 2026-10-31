"""Filesystem locations for the bench harness.

Outputs always follow the *repository checkout*, never the copy of this
package that `uv` installs into `.venv/lib/.../site-packages`.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

_explicit_repo: Path | None = None
_INSTALL_PARTS = frozenset({".venv", "site-packages", "dist-packages"})


def package_dir() -> Path:
    return Path(__file__).resolve().parent


def set_repo_root(path: Path | None) -> None:
    """Override repo discovery (CLI `--repo-root` / tests)."""
    global _explicit_repo
    _explicit_repo = path.resolve() if path is not None else None


def repo_root() -> Path:
    if _explicit_repo is not None:
        return _explicit_repo
    for key in ("WATSON_REPO_ROOT", "WATSON_BENCH_REPO"):
        raw = os.environ.get(key)
        if raw:
            return Path(raw).resolve()

    starts: list[Path] = [Path.cwd().resolve()]
    pkg = package_dir()
    if not _is_install_tree(pkg) and _is_source_package(pkg):
        starts.append(pkg.parent.parent.parent)

    for start in starts:
        found = _walk_for_repo(start)
        if found is not None:
            return found

    git = git_toplevel(Path.cwd())
    if git is not None and not _is_install_tree(git):
        if _looks_like_repo(git) or (git / "pyproject.toml").is_file():
            return git

    raise FileNotFoundError(
        "Could not find the WATSON repo root. Run from the checkout, "
        "pass --repo-root, or set WATSON_REPO_ROOT."
    )


def bench_root() -> Path:
    override = os.environ.get("WATSON_BENCH_ROOT")
    if override:
        return Path(override).resolve()
    return repo_root() / "tools" / "bench"


def candidates_dir() -> Path:
    override = os.environ.get("WATSON_BENCH_CANDIDATES_DIR")
    if override:
        return Path(override)
    checkout = bench_root() / "candidates"
    if checkout.is_dir():
        return checkout
    bundled = package_dir() / "data" / "candidates"
    if bundled.is_dir():
        return bundled
    return checkout


def samples_dir() -> Path:
    override = os.environ.get("WATSON_BENCH_SAMPLES_DIR")
    if override:
        return Path(override)
    checkout = bench_root() / "samples" / "frames"
    if checkout.is_dir():
        return checkout
    bundled = package_dir() / "data" / "samples" / "frames"
    if bundled.is_dir():
        return bundled
    return checkout


def default_models_dir() -> Path:
    override = os.environ.get("WATSON_BENCH_MODELS_DIR")
    if override:
        return Path(override)
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "WATSON" / "models"
    return Path.home() / ".local" / "share" / "watson" / "models"


def default_results_dir() -> Path:
    override = os.environ.get("WATSON_BENCH_RESULTS_DIR")
    if override:
        return Path(override).resolve()
    return repo_root() / "tools" / "bench" / "results"


def default_report_path(date_stamp: str) -> Path:
    override = os.environ.get("WATSON_BENCH_REPORT_DIR")
    if override:
        return Path(override).resolve() / f"{date_stamp}.md"
    return repo_root() / "docs" / "benchmarks" / f"{date_stamp}.md"


def git_toplevel(cwd: Path) -> Path | None:
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=cwd,
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    text = proc.stdout.strip()
    return Path(text) if text else None


def _is_install_tree(path: Path) -> bool:
    return any(part.lower() in _INSTALL_PARTS for part in path.parts)


def _is_source_package(path: Path) -> bool:
    return (
        path.name == "watson_bench"
        and path.parent.name == "bench"
        and path.parent.parent.name == "tools"
    )


def _looks_like_repo(path: Path) -> bool:
    if _is_install_tree(path):
        return False
    return (
        (path / "pyproject.toml").is_file()
        and (path / "tools" / "bench").is_dir()
        and (path / "docs").is_dir()
    )


def _walk_for_repo(start: Path) -> Path | None:
    for parent in (start, *start.parents):
        if _looks_like_repo(parent):
            return parent
    return None
