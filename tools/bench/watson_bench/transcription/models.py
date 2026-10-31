"""Shared on-disk layout for fetch, readiness, and load.

Every engine writes weights under ``<models_dir>/<engine>/<size>/``.
Fetch, ``availability()``, and ``load()`` must use these helpers so a
measured run cannot skip after a successful ``--fetch-only``.
"""

from __future__ import annotations

from pathlib import Path

FASTER_WHISPER_REPOS = {
    "tiny": "Systran/faster-whisper-tiny",
    "base": "Systran/faster-whisper-base",
    "small": "Systran/faster-whisper-small",
    "medium": "Systran/faster-whisper-medium",
}

MLX_WHISPER_REPOS = {
    "tiny": "mlx-community/whisper-tiny",
    "base": "mlx-community/whisper-base",
    "small": "mlx-community/whisper-small",
    "medium": "mlx-community/whisper-medium",
}

_WEIGHT_MARKERS = (
    "model.bin",
    "config.json",
    "weights.npz",
    "weights.safetensors",
    "model.safetensors",
    "fetched.txt",
)


def engine_model_dir(models_dir: Path, engine: str, size: str) -> Path:
    """Canonical directory for one engine/size pair."""
    return models_dir / engine / size


def model_ready(models_dir: Path, engine: str, size: str) -> bool:
    return has_model_weights(engine_model_dir(models_dir, engine, size))


def has_model_weights(path: Path) -> bool:
    if not path.exists():
        return False
    if path.is_file():
        return path.stat().st_size > 0
    if not path.is_dir():
        return False
    if any(
        (path / name).is_file() and (path / name).stat().st_size > 0 for name in _WEIGHT_MARKERS
    ):
        return True
    if first_ggml(path) is not None:
        return True
    return any(child.is_file() and child.stat().st_size > 0 for child in path.rglob("*"))


def first_ggml(directory: Path) -> Path | None:
    if not directory.is_dir():
        return None
    bins = sorted(directory.rglob("*.bin")) + sorted(directory.rglob("*.gguf"))
    return bins[0] if bins else None


def faster_whisper_repo(size: str) -> str:
    try:
        return FASTER_WHISPER_REPOS[size]
    except KeyError as exc:
        raise ValueError(f"no faster-whisper repo for size {size!r}") from exc


def mlx_whisper_repo(size: str) -> str:
    try:
        return MLX_WHISPER_REPOS[size]
    except KeyError as exc:
        raise ValueError(f"no mlx-whisper repo for size {size!r}") from exc
