"""Download Whisper weights. The only transcription path allowed to leave the machine."""

from __future__ import annotations

import os
from pathlib import Path

from watson.privacy.network import ALLOW_NETWORK_ENV
from watson_bench.fetch import FetchError
from watson_bench.transcription.models import (
    engine_model_dir,
    faster_whisper_repo,
    mlx_whisper_repo,
)
from watson_bench.transcription.protocol import TRANSCRIBE_ENGINES, TRANSCRIBE_SIZES


def require_network_allow() -> None:
    if os.environ.get(ALLOW_NETWORK_ENV) != "1":
        raise FetchError(
            f"Set {ALLOW_NETWORK_ENV}=1 to download transcription models. "
            "Measured `watson-bench transcribe` runs stay offline."
        )


def fetch_transcription_model(engine: str, size: str, models_dir: Path) -> str:
    if engine not in TRANSCRIBE_ENGINES or engine == "fake":
        return f"{engine}/{size}: fake runtime needs no weights"
    if size not in TRANSCRIBE_SIZES:
        raise FetchError(f"unknown size {size!r}")
    require_network_allow()
    dest = engine_model_dir(models_dir, engine, size)
    dest.mkdir(parents=True, exist_ok=True)
    if engine == "faster-whisper":
        return _fetch_faster_whisper(size, dest)
    if engine == "mlx-whisper":
        return _fetch_mlx(size, dest)
    if engine == "pywhispercpp":
        return _fetch_pywhispercpp(size, dest)
    raise FetchError(f"cannot fetch engine {engine!r}")


def _fetch_faster_whisper(size: str, dest: Path) -> str:
    try:
        from faster_whisper.utils import download_model
    except ImportError as exc:
        raise FetchError(
            "faster-whisper is not installed. On the Mac: uv sync --group bench --extra transcribe"
        ) from exc
    download_model(size, output_dir=str(dest))
    repo = faster_whisper_repo(size)
    return f"faster-whisper/{size}: downloaded {repo} -> {dest}"


def _fetch_mlx(size: str, dest: Path) -> str:
    try:
        from huggingface_hub import snapshot_download
    except ImportError as exc:
        raise FetchError(
            "huggingface-hub is not installed. On the Mac: uv sync --group bench --extra fetch"
        ) from exc
    repo = mlx_whisper_repo(size)
    snapshot_download(repo_id=repo, local_dir=str(dest))
    return f"mlx-whisper/{size}: downloaded {repo} -> {dest}"


def _fetch_pywhispercpp(size: str, dest: Path) -> str:
    try:
        from pywhispercpp.utils import download_model
    except ImportError:
        download_model = None
    if download_model is not None:
        download_model(size, download_dir=str(dest))
        return f"pywhispercpp/{size}: downloaded -> {dest}"
    try:
        from pywhispercpp.model import Model
    except ImportError as exc:
        raise FetchError(
            "pywhispercpp is not installed. On the Mac: uv sync --group bench --extra pywhispercpp"
        ) from exc
    Model(size, print_realtime=False, print_progress=False)
    marker = dest / "fetched.txt"
    marker.write_text(size + "\n", encoding="utf-8")
    return f"pywhispercpp/{size}: downloaded via Model({size!r}) -> {dest}"
