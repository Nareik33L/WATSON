"""Filesystem locations for the bench harness."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def package_dir() -> Path:
    return Path(__file__).resolve().parent


def bench_root() -> Path:
    """Directory that holds candidates/ and samples/.

    Editable installs keep those next to the package. A wheel install copies
    only watson_bench, so we walk from cwd to the repo checkout.
    """
    override = os.environ.get("WATSON_BENCH_ROOT")
    if override:
        return Path(override)
    local = package_dir().parent
    if _is_bench_root(local):
        return local
    bundled = package_dir() / "data"
    if _is_bench_root(bundled):
        return bundled
    here = Path.cwd().resolve()
    for parent in (here, *here.parents):
        candidate = parent / "tools" / "bench"
        if _is_bench_root(candidate):
            return candidate
    raise FileNotFoundError(
        "Could not find tools/bench/candidates. Run from the WATSON repo root "
        "or set WATSON_BENCH_ROOT / WATSON_BENCH_CANDIDATES_DIR."
    )


def repo_root() -> Path:
    bench = bench_root()
    if bench.name == "bench" and bench.parent.name == "tools":
        return bench.parent.parent
    here = Path.cwd().resolve()
    for parent in (here, *here.parents):
        if (parent / "pyproject.toml").is_file() and (parent / "docs").is_dir():
            return parent
    return Path.cwd()


def candidates_dir() -> Path:
    override = os.environ.get("WATSON_BENCH_CANDIDATES_DIR")
    if override:
        return Path(override)
    return bench_root() / "candidates"


def samples_dir() -> Path:
    override = os.environ.get("WATSON_BENCH_SAMPLES_DIR")
    if override:
        return Path(override)
    return bench_root() / "samples" / "frames"


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
        return Path(override)
    return bench_root() / "results"


def default_report_path(date_stamp: str) -> Path:
    return repo_root() / "docs" / "benchmarks" / f"{date_stamp}.md"


def _is_bench_root(path: Path) -> bool:
    return path.is_dir() and (path / "candidates").is_dir()
