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
    "Your previous output failed validation. Fix every error and return "
    "corrected JSON only.\n"
)


def complete_structured[T: BaseModel](
    runtime: LanguageRuntime,
    messages: Sequence[Message],
    model: type[T],
    *,
    schema: Mapping[str, Any] | None = None,
) -> T:
    """Call ``runtime.complete``, validate, retry once with the error appended."""
    json_schema = dict(schema) if schema is not None else _schema_for(model)
    history = list(messages)
    raw: dict[str, Any] | None = None
    try:
        raw = runtime.complete(history, json_schema)
        return model.model_validate(raw)
    except (ValidationError, OutputParseError) as first:
        repair = str(first)
        assistant = _as_json(raw) if raw is not None else ""
    retry_messages: list[Message] = [*history]
    if assistant:
        retry_messages.append({"role": "assistant", "content": assistant})
    retry_messages.append({"role": "user", "content": _RETRY_PREFIX + repair})
    raw_retry: dict[str, Any] | None = None
    try:
        raw_retry = runtime.complete(retry_messages, json_schema)
        return model.model_validate(raw_retry)
    except (ValidationError, OutputParseError) as second:
        raise StructuredOutputError(
            "structured output failed validation after one retry",
            raw=raw_retry if raw_retry is not None else raw,
            errors=str(second),
        ) from second


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
