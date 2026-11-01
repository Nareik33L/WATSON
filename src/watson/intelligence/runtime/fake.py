"""Deterministic replay runtime: recorded fixtures, no network, no model."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from watson.intelligence.runtime.types import Message

# Valid empty-claims recording used when no fixture file is supplied.
_DEFAULT_COMPLETE: dict[str, Any] = {
    "activity_type": "knowledge_work",
    "description": "No completion evidence is visible.",
    "confidence": 0.7,
    "claims": [],
    "project_candidates": [],
    "people_candidates": [],
}


class FakeRuntime:
    """Language + embedding runtime backed by recorded JSON responses."""

    name = "fake"

    def __init__(
        self,
        *,
        recordings: Path | Mapping[str, Any] | None = None,
        complete_queue: Sequence[object] | None = None,
    ) -> None:
        self._recordings = _load_recordings(recordings)
        self._queue = list(complete_queue or [])
        self.complete_calls = 0

    def probe(self) -> None:
        return None

    def complete(
        self, messages: Sequence[Message], schema: Mapping[str, Any]
    ) -> dict[str, Any]:
        del schema
        self.complete_calls += 1
        if self._queue:
            item = self._queue.pop(0)
            if isinstance(item, BaseException):
                raise item
            if not isinstance(item, dict):
                raise TypeError("fake complete_queue item must be a dict")
            return dict(item)
        blob = "\n".join(item["content"] for item in messages)
        for row in self._recordings.get("complete", []):
            if isinstance(row, dict) and str(row.get("match") or "") in blob:
                response = row.get("response")
                if row.get("match") and isinstance(response, dict):
                    return dict(response)
        default = self._recordings.get("default_complete", _DEFAULT_COMPLETE)
        return dict(default) if isinstance(default, dict) else dict(_DEFAULT_COMPLETE)

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        stored = self._recordings.get("embeddings")
        out: list[list[float]] = []
        for index, text in enumerate(texts):
            raw = stored.get(text) if isinstance(stored, dict) else None
            if isinstance(raw, list):
                out.append([float(x) for x in raw])
            else:
                out.append(
                    [float((index + 1) % 7), float(len(text)), 1.0, float(sum(text.encode()) % 97)]
                )
        return out


def _load_recordings(source: Path | Mapping[str, Any] | None) -> dict[str, Any]:
    if source is None:
        return {"default_complete": _DEFAULT_COMPLETE, "complete": [], "embeddings": {}}
    if isinstance(source, Mapping):
        return dict(source)
    loaded = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise ValueError(f"{source}: recordings must be a JSON object")
    return loaded
