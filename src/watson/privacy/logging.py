"""Structured JSON logs that never emit raw screen-derived content."""

from __future__ import annotations

import hashlib
import json
import logging
import sys
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any, TextIO


class ContentField:
    """Wrap a value derived from screen content.

    ``str()`` / ``repr()`` / JSON encoding always emit length + hash, including
    at DEBUG. The raw value is only available via ``.value`` and must not be
    passed to a logger.
    """

    __slots__ = ("value",)

    def __init__(self, value: str) -> None:
        self.value = value

    def redacted(self) -> str:
        digest = hashlib.sha256(self.value.encode("utf-8")).hexdigest()[:16]
        return f"content[len={len(self.value)} hash={digest}]"

    def __str__(self) -> str:
        return self.redacted()

    def __repr__(self) -> str:
        return f"ContentField({self.redacted()})"


def redact(value: Any) -> Any:
    if isinstance(value, ContentField):
        return value.redacted()
    if isinstance(value, Mapping):
        return {str(key): redact(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(item) for item in value]
    return value


class RedactingJsonFormatter(logging.Formatter):
    """JSON formatter. ContentField is replaced at every level, including DEBUG."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        extra = {
            key: redact(val)
            for key, val in record.__dict__.items()
            if key not in _STANDARD_RECORD_KEYS and not key.startswith("_")
        }
        if extra:
            payload["extra"] = extra
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=_json_default, ensure_ascii=False)


def _json_default(value: Any) -> str:
    if isinstance(value, ContentField):
        return value.redacted()
    return str(value)


_STANDARD_RECORD_KEYS = frozenset(
    {
        "name",
        "msg",
        "args",
        "levelname",
        "levelno",
        "pathname",
        "filename",
        "module",
        "exc_info",
        "exc_text",
        "stack_info",
        "lineno",
        "funcName",
        "created",
        "msecs",
        "relativeCreated",
        "thread",
        "threadName",
        "processName",
        "process",
        "message",
        "taskName",
    }
)


def configure_logging(
    *,
    level: int = logging.INFO,
    stream: TextIO | None = None,
) -> logging.Logger:
    """Install the redacting JSON formatter on the ``watson`` logger."""
    logger = logging.getLogger("watson")
    logger.handlers.clear()
    handler = logging.StreamHandler(stream or sys.stderr)
    handler.setFormatter(RedactingJsonFormatter())
    handler.setLevel(logging.DEBUG)
    logger.addHandler(handler)
    logger.setLevel(level)
    logger.propagate = False
    return logger
