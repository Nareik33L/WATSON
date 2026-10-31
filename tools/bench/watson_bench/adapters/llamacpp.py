"""llama-cpp-python adapter (Metal on the Mac). Optional extra: llamacpp."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from watson_bench.adapters.base import AdapterError, RuntimeCall, Skipped, model_dir_for
from watson_bench.candidates import Candidate
from watson_bench.frames import Frame
from watson_bench.metrics import CallMetrics, estimate_tokens, timed
from watson_bench.prompts import (
    UNDERSTANDING_SCHEMA,
    VLM_SCHEMA,
    parse_json_object,
    understanding_prompt,
    vlm_prompt,
)


class LlamaCppRuntime:
    name = "llamacpp"

    def __init__(self) -> None:
        self._llm: Any = None
        self._loaded: Candidate | None = None

    def availability(self, candidate: Candidate, models_dir: Path) -> Skipped | None:
        try:
            import llama_cpp  # noqa: F401
        except ImportError:
            return Skipped(
                "llama-cpp-python is not installed. "
                "On the Mac: CMAKE_ARGS='-DGGML_METAL=on' uv sync --group bench --extra llamacpp"
            )
        weights = _weights_path(candidate, models_dir)
        if weights is None or not weights.is_file():
            return Skipped(
                f"GGUF weights for {candidate.id} are not on disk. "
                f"Run: watson-bench fetch --profile {candidate.profile} --id {candidate.id}"
            )
        if candidate.role == "vlm":
            mmproj = _mmproj_path(candidate, models_dir)
            if mmproj is None or not mmproj.is_file():
                return Skipped(
                    f"mmproj GGUF for {candidate.id} is not on disk. "
                    f"Run: watson-bench fetch --profile {candidate.profile} --id {candidate.id}"
                )
        return None

    def load(self, candidate: Candidate, models_dir: Path) -> None:
        from llama_cpp import Llama

        weights = _weights_path(candidate, models_dir)
        if weights is None:
            raise AdapterError(f"missing GGUF for {candidate.id}")
        kwargs: dict[str, Any] = {
            "model_path": str(weights),
            "n_ctx": candidate.context_window,
            "n_gpu_layers": -1,
            "verbose": False,
            "embedding": candidate.role == "embeddings",
        }
        if candidate.role == "vlm":
            handler = _vlm_chat_handler(candidate, models_dir)
            if handler is not None:
                kwargs["chat_handler"] = handler
        self._llm = Llama(**kwargs)
        self._loaded = candidate

    def describe(self, candidate: Candidate, frame: Frame) -> RuntimeCall:
        image_uri = f"file://{frame.image_path.resolve()}"
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": vlm_prompt()},
                    {"type": "image_url", "image_url": {"url": image_uri}},
                ],
            }
        ]
        return self._complete(messages, VLM_SCHEMA)

    def complete(self, candidate: Candidate, frame: Frame) -> RuntimeCall:
        del candidate
        messages = [{"role": "user", "content": understanding_prompt(frame)}]
        return self._complete(messages, UNDERSTANDING_SCHEMA)

    def embed(self, candidate: Candidate, texts: list[str]) -> RuntimeCall:
        del candidate
        if self._llm is None:
            raise AdapterError("llama.cpp model is not loaded")
        with timed() as elapsed:
            vectors: list[list[float]] = []
            for text in texts:
                item = self._llm.create_embedding(text)
                data = item.get("data") if isinstance(item, dict) else None
                if isinstance(data, list) and data:
                    vectors.append([float(x) for x in data[0]["embedding"]])
                elif isinstance(item, list):
                    vectors.append([float(x) for x in item])
        return RuntimeCall(
            output=vectors,
            metrics=CallMetrics(latency_s=elapsed(), completion_tokens=0),
            raw_text=f"{len(vectors)} embeddings",
        )

    def unload(self) -> None:
        self._llm = None
        self._loaded = None

    def _complete(self, messages: list[dict[str, Any]], schema: dict[str, Any]) -> RuntimeCall:
        if self._llm is None:
            raise AdapterError("llama.cpp model is not loaded")
        kwargs: dict[str, Any] = {
            "messages": messages,
            "temperature": 0,
            "max_tokens": 512,
        }
        # Newer llama-cpp-python accepts response_format; older builds ignore it.
        kwargs["response_format"] = {"type": "json_object", "schema": schema}
        with timed() as elapsed:
            try:
                result = self._llm.create_chat_completion(**kwargs)
            except TypeError:
                kwargs.pop("response_format", None)
                result = self._llm.create_chat_completion(**kwargs)
        content = _choice_text(result)
        try:
            parsed = parse_json_object(content)
        except (ValueError, json.JSONDecodeError) as exc:
            raise AdapterError(f"llama.cpp returned non-JSON output: {exc}") from exc
        usage = result.get("usage") if isinstance(result, dict) else {}
        completion = 0
        if isinstance(usage, dict):
            completion = int(usage.get("completion_tokens") or 0)
        if completion <= 0:
            completion = estimate_tokens(content)
        return RuntimeCall(
            output=parsed,
            metrics=CallMetrics(latency_s=elapsed(), completion_tokens=completion),
            raw_text=content,
        )


def _weights_path(candidate: Candidate, models_dir: Path) -> Path | None:
    folder = model_dir_for(candidate, models_dir)
    if candidate.filename:
        path = folder / candidate.filename
        return path if path.is_file() else None
    matches = sorted(folder.glob("*.gguf"))
    weights = [path for path in matches if not path.name.startswith("mmproj")]
    return weights[0] if weights else None


def _mmproj_path(candidate: Candidate, models_dir: Path) -> Path | None:
    folder = model_dir_for(candidate, models_dir)
    if candidate.mmproj:
        path = folder / candidate.mmproj
        return path if path.is_file() else None
    matches = sorted(folder.glob("mmproj*.gguf"))
    return matches[0] if matches else None


def _vlm_chat_handler(candidate: Candidate, models_dir: Path) -> Any:
    mmproj = _mmproj_path(candidate, models_dir)
    if mmproj is None:
        raise AdapterError(f"missing mmproj for {candidate.id}")
    try:
        from llama_cpp.llama_chat_format import Qwen25VLChatHandler

        return Qwen25VLChatHandler(clip_model_path=str(mmproj))
    except TypeError:
        from llama_cpp.llama_chat_format import Qwen25VLChatHandler

        return Qwen25VLChatHandler(mmproj_path=str(mmproj))
    except ImportError:
        pass
    try:
        from llama_cpp.llama_chat_format import Llava15ChatHandler

        return Llava15ChatHandler(clip_model_path=str(mmproj))
    except ImportError as exc:
        raise AdapterError(
            "llama-cpp-python has no VLM chat handler. Upgrade the llamacpp extra."
        ) from exc


def _choice_text(result: Any) -> str:
    if not isinstance(result, dict):
        return str(result)
    choices = result.get("choices") or []
    if not choices:
        return ""
    message = choices[0].get("message") or {}
    content = message.get("content")
    if isinstance(content, str):
        return content
    return json.dumps(content) if content is not None else ""
