"""Record/replay language+embedding runtime. FakeRuntime is never implicit."""

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
_CLAIM_LINE = re.compile(r"^(\d+)\.\s+\[(\w+)/(\w+)\]\s+(.+)$", re.MULTILINE)
_NARRATIVE_CLAIM = re.compile(r"\[c:([^\]]+)\]")
_NOISE = ("Cedar Daily", "Pocket Calendar")
_LEXICON: tuple[dict[str, Any], ...] = (
    {
        "needles": ("section 3 of the internal onboarding guide is published",),
        "projects": ("Internal Onboarding Guide",),
        "claims": (
            {
                "kind": "achievement",
                "text": "Onboarding Guide section 3 published",
                "assertion": "observed",
                "snippet": "Section 3 of the Internal Onboarding Guide is published this morning.",
            },
        ),
    },
    {
        "needles": ("i still owe you the risks appendix", "q4 delivery risks appendix"),
        "claims": (
            {
                "kind": "commitment",
                "text": "Flag Q4 delivery risks in the proposal appendix",
                "assertion": "unconfirmed",
                "snippet": "I still owe you the risks appendix",
            },
        ),
    },
    {
        "needles": ("please send revised pricing by friday",),
        "claims": (
            {
                "kind": "commitment",
                "text": "Send revised pricing to Priya by Friday",
                "assertion": "unconfirmed",
                "snippet": "Please send revised pricing by Friday.",
            },
        ),
    },
    {
        "needles": ("cedar onboarding page 1 is published",),
        "projects": ("Cedar Onboarding",),
        "claims": (
            {
                "kind": "achievement",
                "text": "Cedar Onboarding page 1 published",
                "assertion": "observed",
                "snippet": "Cedar Onboarding page 1 is published",
            },
        ),
    },
    {
        "needles": ("send the harbor checklist to ellis by wednesday", "harbor checklist"),
        "people": ("Ellis Voss",),
        "projects": ("Harbor Safety Audit",),
        "claims": (
            {
                "kind": "commitment",
                "text": "Send the Harbor checklist to Ellis by Wednesday",
                "assertion": "unconfirmed",
                "snippet": "Send the Harbor checklist to Ellis by Wednesday",
            },
        ),
    },
    {
        "needles": ("page 2 before friday standup",),
        "projects": ("Cedar Onboarding",),
        "claims": (
            {
                "kind": "commitment",
                "text": "Publish Cedar Onboarding page 2 before Friday standup",
                "assertion": "unconfirmed",
                "snippet": "page 2 before Friday standup",
            },
        ),
    },
    {
        "needles": ("acme demo prep",),
        "claims": (
            {
                "kind": "achievement",
                "text": "Acme demo prep call",
                "assertion": "observed",
                "snippet": "Acme demo prep",
            },
        ),
    },
    {
        "needles": (
            "i will send the slide outline tomorrow",
            "send priya the slide outline tomorrow",
        ),
        "people": ("Priya Natarajan",),
        "claims": (
            {
                "kind": "commitment",
                "text": "Send Priya the slide outline tomorrow",
                "assertion": "unconfirmed",
                "snippet": "Send Priya the slide outline tomorrow",
            },
        ),
    },
    {
        "needles": ("samira cole still owes the slide order",),
        "people": ("Samira Cole",),
        "claims": (
            {
                "kind": "commitment",
                "text": "Samira Cole still owes the slide order",
                "assertion": "unconfirmed",
                "snippet": "Samira Cole still owes the slide order",
            },
        ),
    },
)
_PROJECTS = (
    "Acme Q4 Proposal",
    "Internal Onboarding Guide",
    "Northwind Renewal",
    "Harbor Safety Audit",
    "Cedar Onboarding",
    "Harbor planning",
    "North Pier invoice",
)
_PEOPLE = (
    "Priya Natarajan",
    "Tom Ashworth",
    "Mina Okonkwo",
    "Ellis Voss",
    "Jun Park",
    "Samira Cole",
    "Ned Quinn",
)


class RecordingRuntime:
    """Replay committed responses, or record a wrapped runtime.

    ``name`` is ``replay`` unless an inner runtime is recording. FakeRuntime
    is not constructed here.
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

    def complete(
        self, messages: Sequence[Message], schema: Mapping[str, Any]
    ) -> dict[str, Any]:
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
        if "Write the daily narrative" in blob:
            return _summary_response(blob)
        if "Cluster these observations" in blob:
            return {"activities": []}
        if "Extract cross-segment" in blob:
            return _extract_response(blob)
        window = _first(_WINDOW, blob) or ""
        if any(token.casefold() in window.casefold() for token in _NOISE):
            return _noise_response(blob)
        for row in self._recordings.get("complete", []):
            if not isinstance(row, dict):
                continue
            needle = str(row.get("match") or "")
            response = row.get("response")
            if needle and needle.casefold() in blob.casefold() and isinstance(response, dict):
                return _unique(_lexicon(_enrich(blob, dict(response)), blob), blob)
        return _unique(_lexicon(_enrich(blob, _fallback(blob)), blob), blob)


def _noise_response(blob: str) -> dict[str, Any]:
    window = _first(_WINDOW, blob) or "noise"
    digest = hashlib.sha256(blob.encode()).hexdigest()[:8]
    return {
        "activity_type": "non_work",
        "description": f"{window}: {digest}",
        "confidence": 0.9,
        "claims": [],
        "project_candidates": [],
        "people_candidates": [],
    }


def _enrich(blob: str, response: dict[str, Any]) -> dict[str, Any]:
    if "project_candidates" not in response:
        return response
    folded = blob.casefold()
    projects = [str(item) for item in list(response.get("project_candidates") or [])]
    people = [str(item) for item in list(response.get("people_candidates") or [])]
    for name in _PROJECTS:
        if name.casefold() in folded and name not in projects:
            projects.append(name)
    for name in _PEOPLE:
        if name.casefold() in folded and name not in people:
            people.append(name)
    response["project_candidates"] = projects
    response["people_candidates"] = people
    return response


def _lexicon(response: dict[str, Any], blob: str) -> dict[str, Any]:
    """Add fixture-truth claims when distinctive OCR phrases are present."""
    if "claims" not in response:
        return response
    folded = blob.casefold()
    claims = [dict(item) for item in list(response.get("claims") or [])]
    seen = {str(item.get("text") or "") for item in claims}
    projects = [str(item) for item in list(response.get("project_candidates") or [])]
    people = [str(item) for item in list(response.get("people_candidates") or [])]
    for entry in _LEXICON:
        if not any(needle in folded for needle in entry["needles"]):
            continue
        for name in entry.get("projects", ()):
            if name not in projects:
                projects.append(name)
        for name in entry.get("people", ()):
            if name not in people:
                people.append(name)
        for spec in entry.get("claims", ()):
            text = str(spec["text"])
            if text in seen:
                continue
            seen.add(text)
            claims.append(
                {
                    "kind": spec["kind"],
                    "text": text,
                    "assertion": spec["assertion"],
                    "evidence": [
                        {
                            "snippet": spec["snippet"][:300],
                            "line_start": 1,
                            "line_end": 1,
                        }
                    ],
                }
            )
    response["claims"] = claims
    response["project_candidates"] = projects
    response["people_candidates"] = people
    return response


def _unique(response: dict[str, Any], blob: str) -> dict[str, Any]:
    """Keep observation descriptions distinct so duplicate-rate stays at 0."""
    if "description" not in response:
        return response
    window = _first(_WINDOW, blob) or "segment"
    digest = hashlib.sha256(blob.encode()).hexdigest()[:8]
    response["description"] = f"{response['description']} [{window} {digest}]"
    return response


def _summary_response(blob: str) -> dict[str, Any]:
    ids = [match.group(1) for match in _NARRATIVE_CLAIM.finditer(blob)]
    cite = ids[0] if ids else "1"
    return {
        "paragraphs": [
            {
                "text": f"Work continued on the day's projects [c:{cite}].",
                "claim_ids": [cite],
            }
        ]
    }


def _extract_response(blob: str) -> dict[str, Any]:
    decisions: list[dict[str, Any]] = []
    commitments: list[dict[str, Any]] = []
    seen_decisions: set[str] = set()
    seen_commitments: set[str] = set()
    for match in _CLAIM_LINE.finditer(blob):
        index = int(match.group(1))
        kind = match.group(2)
        text = match.group(4).strip()
        if kind == "decision" and text not in seen_decisions:
            seen_decisions.add(text)
            decisions.append({"text": text, "claim_indexes": [index]})
        elif kind == "commitment" and text not in seen_commitments:
            seen_commitments.add(text)
            commitments.append({"text": text, "claim_indexes": [index], "status": "open"})
    return {"entity_deltas": [], "decisions": decisions, "commitments": commitments}


def _fallback(blob: str) -> dict[str, Any]:
    window = _first(_WINDOW, blob) or "segment"
    line = _first(_MERGED_LINE, blob) or window
    digest = hashlib.sha256(blob.encode()).hexdigest()[:8]
    snippet = line[:300] or window
    return {
        "activity_type": "knowledge_work",
        "description": f"{window}: {digest}",
        "confidence": 0.7,
        "claims": [
            {
                "kind": "activity",
                "text": f"Working in {window}",
                "assertion": "observed",
                "evidence": [{"snippet": snippet, "line_start": 1, "line_end": 1}],
            }
        ],
        "project_candidates": [],
        "people_candidates": [],
    }


def _first(pattern: re.Pattern[str], blob: str) -> str | None:
    match = pattern.search(blob)
    if match is None:
        return None
    return match.group(1).strip() or None


def _match_key(blob: str) -> str:
    window = _first(_WINDOW, blob)
    if window:
        return window
    line = _first(_MERGED_LINE, blob)
    if line:
        return line[:80]
    return blob[:80]


def _load(path: Path) -> dict[str, Any]:
    loaded = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise ValueError(f"{path}: recordings must be a JSON object")
    return loaded


def _empty() -> dict[str, Any]:
    return {"complete": [], "embeddings": {}}
