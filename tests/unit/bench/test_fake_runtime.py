from __future__ import annotations

from pathlib import Path

from watson_bench.adapters.fake import FakeRuntime
from watson_bench.adapters.llamacpp import LlamaCppRuntime
from watson_bench.adapters.mlx import MlxRuntime
from watson_bench.candidates import Candidate
from watson_bench.frames import Frame


def _candidate(role: str) -> Candidate:
    return Candidate(
        id=f"fake-{role}",
        role=role,
        profile="8gb",
        model_id="fake",
        runtime="fake",
        quantisation="n/a",
        context_window=1024,
        min_ram_gb=8,
    )


def test_fake_describe_complete_embed(tmp_path: Path) -> None:
    png = tmp_path / "f001.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\n")
    frame = Frame(
        frame_id="f001",
        image_path=png,
        sidecar={"app_name": "Mail", "window_title": "Inbox"},
        ocr_text="From: Priya",
    )
    runtime = FakeRuntime()
    vlm = _candidate("vlm")
    assert runtime.availability(vlm, tmp_path) is None
    runtime.load(vlm, tmp_path)
    described = runtime.describe(vlm, frame)
    assert described.output["app_guess"] == "Mail"
    assert described.metrics.latency_s > 0

    llm = _candidate("llm")
    runtime.load(llm, tmp_path)
    completed = runtime.complete(llm, frame)
    assert completed.output["claims"][0]["assertion"] == "observed"

    emb = _candidate("embeddings")
    runtime.load(emb, tmp_path)
    embedded = runtime.embed(emb, ["alpha", "beta"])
    assert len(embedded.output) == 2
    runtime.unload()


def test_missing_optional_runtimes_skip_with_message(tmp_path: Path) -> None:
    candidate = _candidate("llm")
    candidate = Candidate(
        id="missing-mlx",
        role="llm",
        profile="8gb",
        model_id="mlx-community/does-not-matter",
        runtime="mlx",
        quantisation="4bit",
        context_window=1024,
        min_ram_gb=8,
    )
    skipped = MlxRuntime().availability(candidate, tmp_path)
    assert skipped is not None
    assert "not installed" in skipped.reason or "not on disk" in skipped.reason

    skipped_llama = LlamaCppRuntime().availability(candidate, tmp_path)
    assert skipped_llama is not None
    assert "not installed" in skipped_llama.reason or "not on disk" in skipped_llama.reason
