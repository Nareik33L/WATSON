"""Ollama adapter: stdlib HTTP to a local server only."""

from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

from watson_bench.adapters.base import AdapterError, RuntimeCall, Skipped
from watson_bench.candidates import Candidate
from watson_bench.frames import Frame
from watson_bench.metrics import CallMetrics, estimate_tokens, timed
from watson_bench.network import (
    DEFAULT_OLLAMA_URL,
    NetworkGuardError,
    ollama_endpoint_or_reason,
)
from watson_bench.prompts import (
    UNDERSTANDING_SCHEMA,
    VLM_SCHEMA,
    parse_json_object,
    understanding_prompt,
    vlm_prompt,
)


class OllamaRuntime:
    name = "ollama"

    def __init__(self, base_url: str | None = None) -> None:
        raw = (
            base_url
            if base_url is not None
            else os.environ.get("OLLAMA_HOST") or DEFAULT_OLLAMA_URL
        )
        endpoint, reason = ollama_endpoint_or_reason(raw)
        self._host_skip = reason
        self.base_url = endpoint.base_url if endpoint is not None else ""
        self._loaded: Candidate | None = None

    def availability(self, candidate: Candidate, models_dir: Path) -> Skipped | None:
        del models_dir
        if self._host_skip:
            return Skipped(self._host_skip)
        try:
            tags = self._request_json("GET", "/api/tags")
        except NetworkGuardError as exc:
            return Skipped(str(exc))
        except AdapterError as exc:
            return Skipped(
                f"Ollama is not reachable at {self.base_url} ({exc}). "
                "Start the local Ollama app and re-run."
            )
        names = _installed_names(tags)
        if candidate.model_id not in names and _bare(candidate.model_id) not in names:
            return Skipped(
                f"Ollama model {candidate.model_id!r} is not pulled. "
                f"Run: watson-bench fetch --profile {candidate.profile} --id {candidate.id}"
            )
        return None

    def load(self, candidate: Candidate, models_dir: Path) -> None:
        del models_dir
        # Warm the model with an empty generate so load time is measurable.
        if candidate.role == "embeddings":
            self._request_json(
                "POST",
                "/api/embed",
                {"model": candidate.model_id, "input": "warmup", "keep_alive": "5m"},
            )
        else:
            payload: dict[str, Any] = {
                "model": candidate.model_id,
                "messages": [{"role": "user", "content": "ping"}],
                "stream": False,
                "keep_alive": "5m",
                "options": {"num_predict": 1, "num_ctx": candidate.context_window},
            }
            self._request_json("POST", "/api/chat", payload)
        self._loaded = candidate

    def describe(self, candidate: Candidate, frame: Frame) -> RuntimeCall:
        image_b64 = base64.b64encode(frame.image_path.read_bytes()).decode("ascii")
        payload = {
            "model": candidate.model_id,
            "messages": [{"role": "user", "content": vlm_prompt(), "images": [image_b64]}],
            "stream": False,
            "format": VLM_SCHEMA,
            "keep_alive": "5m",
            "options": {"temperature": 0, "num_ctx": candidate.context_window},
        }
        return self._chat(payload)

    def complete(self, candidate: Candidate, frame: Frame) -> RuntimeCall:
        payload = {
            "model": candidate.model_id,
            "messages": [{"role": "user", "content": understanding_prompt(frame)}],
            "stream": False,
            "format": UNDERSTANDING_SCHEMA,
            "keep_alive": "5m",
            "options": {"temperature": 0, "num_ctx": candidate.context_window},
        }
        return self._chat(payload)

    def embed(self, candidate: Candidate, texts: list[str]) -> RuntimeCall:
        payload = {"model": candidate.model_id, "input": texts, "keep_alive": "5m"}
        with timed() as elapsed:
            body = self._request_json("POST", "/api/embed", payload)
        embeddings = body.get("embeddings")
        if not isinstance(embeddings, list):
            raise AdapterError("Ollama /api/embed returned no embeddings")
        vectors = [[float(x) for x in row] for row in embeddings]
        return RuntimeCall(
            output=vectors,
            metrics=CallMetrics(latency_s=elapsed(), completion_tokens=0),
            raw_text=f"{len(vectors)} embeddings",
        )

    def unload(self) -> None:
        if self._loaded is None:
            return
        try:
            self._request_json(
                "POST",
                "/api/generate",
                {"model": self._loaded.model_id, "keep_alive": 0},
            )
        except AdapterError:
            pass
        self._loaded = None

    def _chat(self, payload: dict[str, Any]) -> RuntimeCall:
        with timed() as elapsed:
            body = self._request_json("POST", "/api/chat", payload)
        message = body.get("message") if isinstance(body, dict) else None
        content = ""
        if isinstance(message, dict):
            content = str(message.get("content") or "")
        try:
            parsed = parse_json_object(content) if content else {}
        except (ValueError, json.JSONDecodeError) as exc:
            raise AdapterError(f"Ollama returned non-JSON chat output: {exc}") from exc
        eval_count = int(body.get("eval_count") or estimate_tokens(content))
        eval_ns = int(body.get("eval_duration") or 0)
        latency = elapsed()
        if eval_ns > 0:
            latency = max(eval_ns / 1_000_000_000, 1e-6)
        return RuntimeCall(
            output=parsed,
            metrics=CallMetrics(
                latency_s=latency,
                completion_tokens=eval_count,
                prompt_tokens=int(body.get("prompt_eval_count") or 0),
            ),
            raw_text=content,
            extra={"eval_duration_ns": eval_ns},
        )

    def _request_json(
        self, method: str, path: str, payload: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        root = self.base_url if self.base_url.endswith("/") else self.base_url + "/"
        url = urljoin(root, path.lstrip("/"))
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=data,
            method=method,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=600) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise AdapterError(f"Ollama HTTP {exc.code} on {path}: {detail[:300]}") from exc
        except urllib.error.URLError as exc:
            raise AdapterError(str(exc.reason or exc)) from exc
        if not raw:
            return {}
        loaded = json.loads(raw.decode("utf-8"))
        if not isinstance(loaded, dict):
            raise AdapterError(f"Ollama {path} returned a non-object")
        return loaded


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
