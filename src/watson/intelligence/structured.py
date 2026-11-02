"""Schema-constrained complete + Pydantic validate, one retry (plan §7.2)."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

from pydantic import BaseModel, ValidationError

from watson.intelligence.runtime.types import (
    LanguageRuntime,
    Message,
    OutputParseError,
    StructuredOutputError,
)

_RETRY_PREFIX = (
    "Previous output failed validation. Return corrected JSON only. "
    "Do not repeat the source text.\n"
)
REPAIR_NUM_PREDICT = 256
_REPAIR_ERROR_MAX = 400
_REPAIR_ASSISTANT_MAX = 1500


def complete_structured[T: BaseModel](
    runtime: LanguageRuntime,
    messages: Sequence[Message],
    model: type[T],
    *,
    schema: Mapping[str, Any] | None = None,
) -> T:
    """Call ``runtime.complete``, validate, retry once with a compact repair prompt."""
    json_schema = dict(schema) if schema is not None else _schema_for(model)
    history = list(messages)
    raw: dict[str, Any] | None = None
    try:
        raw = _complete(runtime, history, json_schema)
        return model.model_validate(raw)
    except (ValidationError, OutputParseError) as first:
        repair = str(first)
        assistant = _as_json(raw) if raw is not None else ""
    retry_messages = _repair_messages(history, assistant, repair)
    raw_retry: dict[str, Any] | None = None
    try:
        raw_retry = _complete(runtime, retry_messages, json_schema, num_predict=REPAIR_NUM_PREDICT)
        return model.model_validate(raw_retry)
    except (ValidationError, OutputParseError) as second:
        raise StructuredOutputError(
            "structured output failed validation after one retry",
            raw=raw_retry if raw_retry is not None else raw,
            errors=str(second),
        ) from second


def _complete(
    runtime: LanguageRuntime,
    messages: Sequence[Message],
    schema: Mapping[str, Any],
    *,
    num_predict: int | None = None,
) -> dict[str, Any]:
    complete = runtime.complete
    try:
        loaded = complete(messages, schema, num_predict=num_predict)
    except TypeError as exc:
        if "num_predict" not in str(exc):
            raise
        loaded = complete(messages, schema)
    if not isinstance(loaded, dict):
        raise OutputParseError("Ollama chat returned a non-object JSON value")
    return loaded


def _repair_messages(history: Sequence[Message], assistant: str, error: str) -> list[Message]:
    """System + truncated assistant JSON + compact error. No original OCR."""
    retry: list[Message] = []
    for item in history:
        if item.get("role") == "system":
            retry.append(item)
            break
    if assistant:
        retry.append({"role": "assistant", "content": assistant[:_REPAIR_ASSISTANT_MAX]})
    detail = " ".join(error.split())[:_REPAIR_ERROR_MAX]
    retry.append({"role": "user", "content": _RETRY_PREFIX + detail})
    return retry


def _schema_for(model: type[BaseModel]) -> dict[str, Any]:
    custom = getattr(model, "json_schema", None)
    if callable(custom):
        loaded = custom()
        if isinstance(loaded, dict):
            return dict(loaded)
    return dict(model.model_json_schema())


def _as_json(value: object) -> str:
    try:
        return json.dumps(value, ensure_ascii=False)
    except TypeError:
        return str(value)
