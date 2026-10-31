from __future__ import annotations

import json
from pathlib import Path

import pytest

from watson_bench.adapters.ollama import OllamaRuntime
from watson_bench.candidates import Candidate
from watson_bench.network import parse_ollama_host
from watson_bench.paths import samples_dir
from watson_bench.run import run_bakeoff

VALID_HOSTS = (
    ("127.0.0.1:11434", "http://127.0.0.1:11434", "127.0.0.1", 11434),
    ("localhost:11434", "http://localhost:11434", "localhost", 11434),
    ("127.0.0.1", "http://127.0.0.1:11434", "127.0.0.1", 11434),
    ("localhost", "http://localhost:11434", "localhost", 11434),
    ("http://127.0.0.1:11434", "http://127.0.0.1:11434", "127.0.0.1", 11434),
    ("http://localhost:11434", "http://localhost:11434", "localhost", 11434),
    ("https://127.0.0.1:11434", "https://127.0.0.1:11434", "127.0.0.1", 11434),
    ("http://127.0.0.1", "http://127.0.0.1:11434", "127.0.0.1", 11434),
    ("[::1]:11434", "http://[::1]:11434", "::1", 11434),
    ("http://[::1]:11434", "http://[::1]:11434", "::1", 11434),
)

INVALID_HOSTS = (
    "example.com:11434",
    "http://example.com:11434",
    "8.8.8.8:11434",
    "http://8.8.8.8:11434",
    "",
    "://bad",
    "ftp://127.0.0.1:11434",
)


def _candidate() -> Candidate:
    return Candidate(
        id="ollama-llama32-3b",
        role="llm",
        profile="8gb",
        model_id="llama3.2:3b",
        runtime="ollama",
        quantisation="q4_K_M",
        context_window=8192,
        min_ram_gb=8,
    )


@pytest.mark.parametrize("raw, base, host, port", VALID_HOSTS)
def test_parse_ollama_host_formats(raw: str, base: str, host: str, port: int) -> None:
    parsed = parse_ollama_host(raw)
    assert parsed.base_url == base
    assert parsed.host == host
    assert parsed.port == port


@pytest.mark.parametrize("raw, base, host, port", VALID_HOSTS)
def test_ollama_runtime_accepts_each_format(
    raw: str, base: str, host: str, port: int, tmp_path: Path
) -> None:
    runtime = OllamaRuntime(raw)
    assert runtime._host_skip is None
    assert runtime.base_url == base
    skip = runtime.availability(_candidate(), tmp_path)
    assert skip is not None
    assert "not a loopback" not in skip.reason
    assert "Invalid OLLAMA_HOST" not in skip.reason
    _ = host, port


@pytest.mark.parametrize("raw", INVALID_HOSTS)
def test_ollama_runtime_skips_invalid_or_remote_host(raw: str, tmp_path: Path) -> None:
    runtime = OllamaRuntime(raw)
    skip = runtime.availability(_candidate(), tmp_path)
    assert skip is not None
    assert "Invalid OLLAMA_HOST" in skip.reason or "loopback" in skip.reason


def test_ollama_constructor_does_not_raise_on_remote_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("OLLAMA_HOST", "example.com:11434")
    runtime = OllamaRuntime()
    skip = runtime.availability(_candidate(), tmp_path)
    assert skip is not None
    assert "loopback" in skip.reason


def test_run_skips_ollama_when_host_is_remote(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OLLAMA_HOST", "http://8.8.8.8:11434")
    out = run_bakeoff(
        profile="8gb",
        frames_dir=samples_dir(),
        out_dir=tmp_path / "ollama-skip",
        runtimes=["ollama"],
        roles=["llm"],
    )
    manifest = json.loads((out / "run.json").read_text(encoding="utf-8"))
    assert manifest["results"]
    assert all(row["skipped"] for row in manifest["results"])
    assert all("loopback" in (row["skip_reason"] or "") for row in manifest["results"])
    assert all(row["error"] is None for row in manifest["results"])
