"""Deterministic in-process runtime for Linux CI and harness tests."""

from __future__ import annotations

from pathlib import Path

from watson_bench.adapters.base import RuntimeCall, Skipped
from watson_bench.candidates import Candidate
from watson_bench.frames import Frame
from watson_bench.metrics import CallMetrics, estimate_tokens


class FakeRuntime:
    name = "fake"

    def __init__(self) -> None:
        self._loaded: Candidate | None = None

    def availability(self, candidate: Candidate, models_dir: Path) -> Skipped | None:
        del candidate, models_dir
        return None

    def load(self, candidate: Candidate, models_dir: Path) -> None:
        del models_dir
        self._loaded = candidate

    def describe(self, candidate: Candidate, frame: Frame) -> RuntimeCall:
        self._require(candidate)
        output = {
            "visible_text": frame.ocr_text or frame.window_title or frame.frame_id,
            "ui_description": f"synthetic {frame.app_name or 'unknown'} window",
            "app_guess": frame.app_name or "unknown",
            "visual_density": "text",
        }
        return self._call(output)

    def complete(self, candidate: Candidate, frame: Frame) -> RuntimeCall:
        self._require(candidate)
        snippet = (frame.ocr_text.splitlines() or [frame.window_title or frame.frame_id])[0]
        output = {
            "activity_type": "knowledge_work",
            "description": (
                f"Working in {frame.app_name or 'an app'} "
                f"on {frame.window_title or frame.frame_id}."
            ),
            "claims": [
                {
                    "kind": "activity",
                    "text": f"Visible work in {frame.app_name or 'unknown'}",
                    "assertion": "observed",
                    "evidence_snippet": snippet[:300],
                }
            ],
        }
        return self._call(output)

    def embed(self, candidate: Candidate, texts: list[str]) -> RuntimeCall:
        self._require(candidate)
        vectors = [
            [float((index + 1) % 7), float(len(text)), 1.0]
            for index, text in enumerate(texts)
        ]
        raw = f"{len(texts)} texts"
        return RuntimeCall(
            output=vectors,
            metrics=CallMetrics(latency_s=0.001, completion_tokens=0),
            raw_text=raw,
        )

    def unload(self) -> None:
        self._loaded = None

    def _require(self, candidate: Candidate) -> None:
        if self._loaded is None or self._loaded.id != candidate.id:
            raise RuntimeError(f"fake runtime has not loaded {candidate.id}")

    def _call(self, output: dict) -> RuntimeCall:
        import json

        raw = json.dumps(output, sort_keys=True)
        return RuntimeCall(
            output=output,
            metrics=CallMetrics(latency_s=0.002, completion_tokens=estimate_tokens(raw)),
            raw_text=raw,
        )
