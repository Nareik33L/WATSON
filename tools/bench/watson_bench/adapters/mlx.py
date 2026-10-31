"""MLX adapter (mlx-lm / mlx-vlm / mlx-embeddings). Optional extra: mlx."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from watson_bench.adapters.base import AdapterError, RuntimeCall, Skipped, model_dir_for
from watson_bench.candidates import Candidate
from watson_bench.frames import Frame
from watson_bench.metrics import CallMetrics, estimate_tokens, timed
from watson_bench.prompts import parse_json_object, understanding_prompt, vlm_prompt


class MlxRuntime:
    name = "mlx"

    def __init__(self) -> None:
        self._model: Any = None
        self._tokenizer: Any = None
        self._processor: Any = None
        self._model_dir: str | None = None
        self._loaded: Candidate | None = None

    def availability(self, candidate: Candidate, models_dir: Path) -> Skipped | None:
        missing = _missing_extra(candidate.role)
        if missing:
            return Skipped(missing)
        folder = model_dir_for(candidate, models_dir)
        if not _looks_like_model_dir(folder):
            return Skipped(
                f"MLX weights for {candidate.id} are not on disk. "
                f"Run: watson-bench fetch --profile {candidate.profile} --id {candidate.id}"
            )
        return None

    def load(self, candidate: Candidate, models_dir: Path) -> None:
        folder = str(model_dir_for(candidate, models_dir))
        self._model_dir = folder
        if candidate.role == "vlm":
            from mlx_vlm import load as load_vlm

            self._model, self._processor = load_vlm(folder)
        elif candidate.role == "embeddings":
            from mlx_embeddings import load as load_embed

            self._model, self._tokenizer = load_embed(folder)
        else:
            from mlx_lm import load as load_lm

            self._model, self._tokenizer = load_lm(folder)
        self._loaded = candidate

    def describe(self, candidate: Candidate, frame: Frame) -> RuntimeCall:
        del candidate
        from mlx_vlm import generate
        from mlx_vlm.prompt_utils import apply_chat_template
        from mlx_vlm.utils import load_config

        if self._model is None or self._processor is None or self._model_dir is None:
            raise AdapterError("MLX model is not loaded")
        config = getattr(self._model, "config", None) or load_config(self._model_dir)
        prompt = apply_chat_template(self._processor, config, vlm_prompt(), num_images=1)
        with timed() as elapsed:
            raw = generate(
                self._model,
                self._processor,
                prompt,
                image=str(frame.image_path),
                max_tokens=256,
                temp=0.0,
            )
        return _json_call(_coerce_text(raw), elapsed())

    def complete(self, candidate: Candidate, frame: Frame) -> RuntimeCall:
        del candidate
        from mlx_lm import generate

        if self._model is None or self._tokenizer is None:
            raise AdapterError("MLX model is not loaded")
        messages = [{"role": "user", "content": understanding_prompt(frame)}]
        if getattr(self._tokenizer, "chat_template", None):
            prompt = self._tokenizer.apply_chat_template(messages, add_generation_prompt=True)
        else:
            prompt = understanding_prompt(frame)
        with timed() as elapsed:
            raw = generate(self._model, self._tokenizer, prompt=prompt, max_tokens=256)
        return _json_call(_coerce_text(raw), elapsed())

    def embed(self, candidate: Candidate, texts: list[str]) -> RuntimeCall:
        del candidate
        from mlx_embeddings import generate

        if self._model is None or self._tokenizer is None:
            raise AdapterError("MLX model is not loaded")
        with timed() as elapsed:
            output = generate(self._model, self._tokenizer, texts=texts)
        embeds = getattr(output, "text_embeds", output)
        vectors = _as_vectors(embeds)
        return RuntimeCall(
            output=vectors,
            metrics=CallMetrics(latency_s=elapsed(), completion_tokens=0),
            raw_text=f"{len(texts)} embeddings",
        )

    def unload(self) -> None:
        self._model = None
        self._tokenizer = None
        self._processor = None
        self._model_dir = None
        self._loaded = None


def _as_vectors(embeds: Any) -> list[list[float]]:
    if hasattr(embeds, "tolist"):
        raw = embeds.tolist()
        return [[float(x) for x in row] for row in raw]
    return [[float(x) for x in row] for row in embeds]


def _missing_extra(role: str) -> str | None:
    if role == "vlm":
        try:
            import mlx_vlm  # noqa: F401
        except ImportError:
            return "mlx-vlm is not installed. On the Mac: uv sync --group bench --extra mlx"
        return None
    if role == "embeddings":
        try:
            import mlx_embeddings  # noqa: F401
        except ImportError:
            return "mlx-embeddings is not installed. On the Mac: uv sync --group bench --extra mlx"
        return None
    try:
        import mlx_lm  # noqa: F401
    except ImportError:
        return "mlx-lm is not installed. On the Mac: uv sync --group bench --extra mlx"
    return None


def _looks_like_model_dir(folder: Path) -> bool:
    if not folder.is_dir():
        return False
    markers = ("config.json", "model.safetensors", "model.safetensors.index.json", "weights.npz")
    return any((folder / name).exists() for name in markers) or any(folder.glob("*.safetensors"))


def _coerce_text(raw: Any) -> str:
    if isinstance(raw, str):
        return raw
    if isinstance(raw, dict):
        for key in ("text", "output", "generation"):
            if isinstance(raw.get(key), str):
                return raw[key]
        return json.dumps(raw)
    return str(raw)


def _json_call(text: str, latency_s: float) -> RuntimeCall:
    try:
        parsed = parse_json_object(text)
    except (ValueError, json.JSONDecodeError) as exc:
        raise AdapterError(f"MLX returned non-JSON output: {exc}") from exc
    return RuntimeCall(
        output=parsed,
        metrics=CallMetrics(latency_s=latency_s, completion_tokens=estimate_tokens(text)),
        raw_text=text,
    )
