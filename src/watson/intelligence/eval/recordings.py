"""Record/replay language+embedding runtime. FakeRuntime is never implicit.

Replay plays back committed JSON responses with no truth.yaml lexicon,
no name-list enrichment, and no description hashing. Recordings are
hand-authored plausible model output (see recordings.json ``source``).
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from watson.intelligence.runtime.types import Message, RuntimeUnavailable

DEFAULT_RECORDINGS = (
    Path(__file__).resolve().parents[4] / "tests" / "fixtures" / "eval" / "recordings.json"
)

_WINDOW = re.compile(r"^Window:\s*(.+)$", re.MULTILINE)
_MERGED_LINE = re.compile(r"^\d+\.\s+(.+)$", re.MULTILINE)

# Generic understanding when no recording matches. Not a quality oracle.
_DEFAULT_UNDERSTAND: dict[str, Any] = {
    "activity_type": "knowledge_work",
    "description": "Working in the frontmost window. No completion evidence is visible.",
    "confidence": 0.7,
    "claims": [
        {
            "kind": "activity",
            "text": "Editing a document",
            "assertion": "observed",
            "evidence": [
                {"snippet": "Still working in the current window.", "line_start": 1, "line_end": 1}
            ],
        }
    ],
    "project_candidates": [],
    "people_candidates": [],
}


class RecordingRuntime:
    """Replay committed responses, or record a wrapped runtime.

    ``name`` is ``replay`` unless an inner runtime is recording. FakeRuntime
    is not constructed here. Replay does not rewrite responses.
    """

    def __init__(
        self,
        *,
        path: Path | None = None,
        inner: Any | None = None,
        mode: str = "replay",
    ) -> None:
        if mode not in {"replay", "record"}:
            raise ValueError('eval mode must be "replay" or "record"')
        if mode == "record" and inner is None:
            raise RuntimeUnavailable(
                "record mode needs an explicit runtime (ollama). "
                "FakeRuntime is opt-in only via --runtime fake."
            )
        self.mode = mode
        self.path = path or DEFAULT_RECORDINGS
        self.inner = inner
        self.name = "replay" if inner is None else str(getattr(inner, "name", "record"))
        self._recordings = _load(self.path) if self.path.is_file() else _empty()
        self._dirty = False
        self.complete_calls = 0

    def probe(self) -> None:
        probe = getattr(self.inner, "probe", None)
        if callable(probe):
            probe()

    def complete(self, messages: Sequence[Message], schema: Mapping[str, Any]) -> dict[str, Any]:
        self.complete_calls += 1
        blob = "\n".join(item["content"] for item in messages)
        if self.mode == "replay":
            return self._replay(blob)
        assert self.inner is not None
        result = self.inner.complete(messages, schema)
        if not isinstance(result, dict):
            raise TypeError("recorded complete() must return an object")
        self._recordings.setdefault("complete", []).append(
            {"match": _match_key(blob), "response": dict(result)}
        )
        self._dirty = True
        return dict(result)

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        if self.mode == "record":
            assert self.inner is not None
            vectors = self.inner.embed(texts)
            stored = self._recordings.setdefault("embeddings", {})
            recorded: list[list[float]] = []
            for text, vector in zip(texts, vectors, strict=False):
                row = [float(x) for x in vector]
                stored[text] = row
                recorded.append(row)
            self._dirty = True
            return recorded
        stored = self._recordings.get("embeddings")
        out: list[list[float]] = []
        for text in texts:
            raw = stored.get(text) if isinstance(stored, dict) else None
            if isinstance(raw, list):
                out.append([float(x) for x in raw])
            else:
                # Signed hash dims so distinct names are not cosine-collapsed
                # into the first entity of that type (plan D8 resolver).
                digest = hashlib.sha256(text.encode()).digest()
                out.append([(byte - 128.0) / 128.0 for byte in digest[:16]])
        return out

    def save(self) -> None:
        if not self._dirty:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self._recordings, indent=2) + "\n", encoding="utf-8")
        self._dirty = False

    def _replay(self, blob: str) -> dict[str, Any]:
        """First matching recording wins. No post-processing.

        Understand prompts are keyed by the ``Window:`` line so nav chrome
        (e.g. Cedar Daily in an intranet page) cannot steal the match.
        Summary / cluster / extract have no Window line and match the blob.
        """
        window = _WINDOW.search(blob)
        haystack = window.group(1).strip() if window else blob
        for row in self._recordings.get("complete", []):
            if not isinstance(row, dict):
                continue
            needle = str(row.get("match") or "")
            response = row.get("response")
            if needle and needle.casefold() in haystack.casefold() and isinstance(response, dict):
                return dict(response)
        return dict(_DEFAULT_UNDERSTAND)


def _match_key(blob: str) -> str:
    match = _WINDOW.search(blob)
    if match and match.group(1).strip():
        return match.group(1).strip()
    line = _MERGED_LINE.search(blob)
    if line and line.group(1).strip():
        return line.group(1).strip()[:80]
    return blob[:80]


def _load(path: Path) -> dict[str, Any]:
    loaded = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise ValueError(f"{path}: recordings must be a JSON object")
    return loaded


def _empty() -> dict[str, Any]:
    return {"complete": [], "embeddings": {}, "source": "empty"}
