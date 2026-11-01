"""Ollama LanguageRuntime + EmbeddingRuntime. Loopback HTTP only (ADR 0001)."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

from watson.config.schema import IntelligenceConfig
from watson.intelligence.runtime.http import canonical_ollama_url, request_json
from watson.intelligence.runtime.types import (
    Message,
    RuntimeCallError,
    RuntimeUnavailable,
)


class OllamaRuntime:
    """``complete`` / ``embed`` against a local Ollama server via ``format``."""

    name = "ollama"

    def __init__(self, settings: IntelligenceConfig) -> None:
        self._settings = settings
        self.base_url = canonical_ollama_url(settings.ollama_host)
        self.llm_model = settings.llm_model
        self.embedding_model = settings.embedding_model

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
                f"Ollama model(s) missing at {self.base_url}: {', '.join(missing)}. "
                f"Run: {pulls}",
                fetch_command=pulls,
            )

    def complete(
        self, messages: Sequence[Message], schema: Mapping[str, Any]
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.llm_model,
            "messages": [dict(item) for item in messages],
            "stream": False,
            "format": dict(schema),
            "keep_alive": self._settings.keep_alive,
            "options": {
                "temperature": self._settings.temperature,
                "num_ctx": self._settings.context_window,
            },
        }
        body = request_json(self.base_url, "POST", "/api/chat", payload)
        message = body.get("message") if isinstance(body, dict) else None
        content = ""
        if isinstance(message, dict):
            content = str(message.get("content") or "")
        parsed = _parse_json_object(content)
        if not isinstance(parsed, dict):
            raise RuntimeCallError("Ollama chat returned a non-object JSON value")
        return parsed

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        body = request_json(
            self.base_url,
            "POST",
            "/api/embed",
            {
                "model": self.embedding_model,
                "input": list(texts),
                "keep_alive": self._settings.keep_alive,
            },
            timeout=60.0,
        )
        embeddings = body.get("embeddings")
        if not isinstance(embeddings, list):
            raise RuntimeCallError("Ollama /api/embed returned no embeddings")
        return [[float(x) for x in row] for row in embeddings]


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
        raise RuntimeCallError("Ollama returned an empty chat body")
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
            raise RuntimeCallError("Ollama returned non-JSON chat output") from None
        loaded = json.loads(cleaned[start : end + 1])
    if not isinstance(loaded, dict):
        raise RuntimeCallError("Ollama chat output is not a JSON object")
    return loaded
