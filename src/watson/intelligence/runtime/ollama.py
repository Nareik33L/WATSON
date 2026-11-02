"""Ollama LanguageRuntime + EmbeddingRuntime. Loopback HTTP only (ADR 0001)."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any, Literal

from watson.config.schema import IntelligenceConfig
from watson.intelligence.runtime.http import canonical_ollama_url, request_json
from watson.intelligence.runtime.types import (
    Message,
    OutputParseError,
    RuntimeCallError,
    RuntimeUnavailable,
)

Phase = Literal["chat", "embed"]


class OllamaRuntime:
    """``complete`` / ``embed`` against a local Ollama server via ``format``.

    When ``unload_between_phases`` is on, at most one model is resident
    (§11.4). ``keep_alive: 0`` runs at sub-phase boundaries (understanding
    chat → understanding embed → consolidation → summary) and whenever a
    call would otherwise load a second model.
    """

    name = "ollama"

    def __init__(self, settings: IntelligenceConfig) -> None:
        self._settings = settings
        self.base_url = canonical_ollama_url(settings.ollama_host)
        self.llm_model = settings.llm_model
        self.embedding_model = settings.embedding_model
        self._used: set[Phase] = set()

    def probe(self) -> None:
        """Raise RuntimeUnavailable if the server or a configured model is missing."""
        tags = request_json(self.base_url, "GET", "/api/tags", timeout=5.0)
        names = _installed_names(tags)
        missing: list[str] = []
        for model in (self.llm_model, self.embedding_model):
            if model not in names and _bare(model) not in names:
                missing.append(model)
        if missing:
            pulls = " && ".join(f"ollama pull {name}" for name in missing)
            raise RuntimeUnavailable(
                f"Ollama model(s) missing at {self.base_url}: {', '.join(missing)}. Run: {pulls}",
                fetch_command=pulls,
            )

    def complete(self, messages: Sequence[Message], schema: Mapping[str, Any]) -> dict[str, Any]:
        self._ensure_resident("chat")
        payload: dict[str, Any] = {
            "model": self.llm_model,
            "messages": [dict(item) for item in messages],
            "stream": False,
            "format": dict(schema),
            "keep_alive": self._settings.keep_alive,
            "options": {
                "temperature": self._settings.temperature,
                "num_ctx": self._settings.ollama_num_ctx(),
                "num_predict": int(self._settings.ollama.num_predict),
            },
        }
        body = request_json(self.base_url, "POST", "/api/chat", payload, timeout=self._timeout())
        message = body.get("message") if isinstance(body, dict) else None
        content = ""
        if isinstance(message, dict):
            content = str(message.get("content") or "")
        parsed = _parse_json_object(content)
        if not isinstance(parsed, dict):
            raise OutputParseError("Ollama chat returned a non-object JSON value")
        return parsed

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        self._ensure_resident("embed")
        body = request_json(
            self.base_url,
            "POST",
            "/api/embed",
            {
                "model": self.embedding_model,
                "input": list(texts),
                "keep_alive": self._settings.keep_alive,
            },
            timeout=self._timeout(),
        )
        embeddings = body.get("embeddings")
        if not isinstance(embeddings, list):
            raise RuntimeCallError("Ollama /api/embed returned no embeddings")
        return [[float(x) for x in row] for row in embeddings]

    def _timeout(self) -> float:
        return float(self._settings.ollama.timeout_s)

    def end_phase(self) -> None:
        """Unload models used in this sub-phase. No-op when unloading is disabled."""
        if not self._settings.ollama.unload_between_phases:
            self._used.clear()
            return
        if "chat" in self._used:
            self._unload(self.llm_model)
        if "embed" in self._used:
            self._unload(self.embedding_model)
        self._used.clear()

    def _ensure_resident(self, kind: Phase) -> None:
        """Unload the other model before loading ``kind`` so the set never exceeds one."""
        other: Phase = "embed" if kind == "chat" else "chat"
        if other in self._used and self._settings.ollama.unload_between_phases:
            self._unload(self.llm_model if other == "chat" else self.embedding_model)
            self._used.discard(other)
        self._used.add(kind)

    def _unload(self, model: str) -> None:
        """Drop a resident model (Ollama ``keep_alive: 0``)."""
        request_json(
            self.base_url,
            "POST",
            "/api/generate",
            {"model": model, "keep_alive": 0},
            timeout=self._timeout(),
        )


def _installed_names(tags: dict[str, Any]) -> set[str]:
    names: set[str] = set()
    models = tags.get("models")
    if not isinstance(models, list):
        return names
    for model in models:
        if isinstance(model, dict):
            name = str(model.get("name") or "")
            if name:
                names.add(name)
                names.add(_bare(name))
    return names


def _bare(name: str) -> str:
    return name.split(":")[0]


def _parse_json_object(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    if not cleaned:
        raise OutputParseError("Ollama returned an empty chat body")
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()
    try:
        loaded: Any = json.loads(cleaned)
    except json.JSONDecodeError:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start < 0 or end <= start:
            raise OutputParseError("Ollama returned non-JSON chat output") from None
        try:
            loaded = json.loads(cleaned[start : end + 1])
        except json.JSONDecodeError:
            raise OutputParseError("Ollama returned non-JSON chat output") from None
    if not isinstance(loaded, dict):
        raise OutputParseError("Ollama chat output is not a JSON object")
    return loaded
