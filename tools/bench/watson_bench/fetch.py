"""Explicit model download. The only command allowed to leave the machine."""

from __future__ import annotations

import json
import shutil
import subprocess
import urllib.request
from pathlib import Path

from watson_bench.adapters.base import model_dir_for
from watson_bench.candidates import Candidate
from watson_bench.network import assert_loopback_url

DEFAULT_OLLAMA_URL = "http://127.0.0.1:11434"


class FetchError(RuntimeError):
    """A requested download failed."""


def fetch_candidate(candidate: Candidate, models_dir: Path) -> str:
    if candidate.runtime == "fake":
        return f"{candidate.id}: fake runtime needs no weights"
    if candidate.runtime == "ollama":
        return _fetch_ollama(candidate)
    if candidate.runtime in {"llamacpp", "mlx"}:
        return _fetch_huggingface(candidate, models_dir)
    raise FetchError(f"cannot fetch runtime {candidate.runtime!r}")


def _fetch_ollama(candidate: Candidate) -> str:
    assert_loopback_url(DEFAULT_OLLAMA_URL)
    if shutil.which("ollama"):
        proc = subprocess.run(
            ["ollama", "pull", candidate.model_id],
            check=False,
        )
        if proc.returncode != 0:
            raise FetchError(f"ollama pull {candidate.model_id} failed (exit {proc.returncode})")
        return f"{candidate.id}: pulled {candidate.model_id} via ollama CLI"
    payload = json.dumps({"name": candidate.model_id, "stream": False}).encode("utf-8")
    request = urllib.request.Request(
        f"{DEFAULT_OLLAMA_URL}/api/pull",
        data=payload,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=3600) as response:
            response.read()
    except OSError as exc:
        raise FetchError(
            f"Ollama pull of {candidate.model_id} failed ({exc}). "
            "Install the Ollama app or put `ollama` on PATH."
        ) from exc
    return f"{candidate.id}: pulled {candidate.model_id} via localhost /api/pull"


def _fetch_huggingface(candidate: Candidate, models_dir: Path) -> str:
    try:
        from huggingface_hub import snapshot_download
    except ImportError as exc:
        raise FetchError(
            "huggingface-hub is not installed. On the Mac: uv sync --group bench --extra fetch"
        ) from exc
    dest = model_dir_for(candidate, models_dir)
    dest.mkdir(parents=True, exist_ok=True)
    patterns: list[str] | None = None
    if candidate.runtime == "llamacpp":
        patterns = [name for name in (candidate.filename, candidate.mmproj) if name]
        if not patterns:
            patterns = ["*.gguf", "*.json", "README.md"]
    snapshot_download(
        repo_id=candidate.model_id,
        local_dir=str(dest),
        allow_patterns=patterns,
    )
    return f"{candidate.id}: downloaded {candidate.model_id} -> {dest}"
