"""Runtime protocols and typed job failures (plan D6 / §7.1 / §7.4)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Literal, Protocol, TypedDict


class Message(TypedDict):
    """One chat turn. Roles match the Ollama / OpenAI chat vocabulary."""

    role: Literal["system", "user", "assistant"]
    content: str


class JobFailure(Exception):
    """Typed failure a worker records on a job. Does not crash the process."""

    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


class RuntimeUnavailable(JobFailure):
    """Runtime or model is missing. Understanding jobs stay queued (§7.4)."""

    def __init__(self, message: str, *, fetch_command: str | None = None) -> None:
        super().__init__(message, retryable=True)
        self.fetch_command = fetch_command


class RuntimeCallError(JobFailure):
    """The runtime was reachable but the call failed."""

    def __init__(self, message: str) -> None:
        super().__init__(message, retryable=True)


class OutputParseError(RuntimeCallError):
    """Chat body was not a JSON object. One repair retry, then terminal."""


class StructuredOutputError(JobFailure):
    """Pydantic validation failed after the single retry (plan §7.2)."""

    def __init__(self, message: str, *, raw: object, errors: str) -> None:
        super().__init__(message, retryable=False)
        self.raw = raw
        self.errors = errors


LAST_ERROR_MAX = 240


def format_job_error(exc: BaseException, *, limit: int = LAST_ERROR_MAX) -> str:
    """``Type: truncated message`` for ``jobs.last_error``. Never stores raw model output."""
    name = type(exc).__name__
    message = " ".join(str(exc).split())
    if not message:
        return name
    text = f"{name}: {message}"
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def end_runtime_phase(runtime: object) -> None:
    """Unload resident models at a phase boundary. No-op if unsupported."""
    end = getattr(runtime, "end_phase", None)
    if callable(end):
        end()


class LanguageRuntime(Protocol):
    def complete(
        self,
        messages: Sequence[Message],
        schema: Mapping[str, Any],
        *,
        num_predict: int | None = None,
    ) -> dict[str, Any]:
        """Schema-constrained completion. Returns a JSON object."""


class EmbeddingRuntime(Protocol):
    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """Embed ``texts`` in order. Empty input returns an empty list."""


class VisionRuntime(Protocol):
    """Stub only. VLM is off on 8 GB (ADR 0001)."""

    def describe(self, image: bytes, schema: Mapping[str, Any]) -> dict[str, Any]:
        """Constrained visual description. Not implemented on 8 GB."""


class DisabledVisionRuntime:
    """ADR 0001: VLM stays off on the 8 GB V1.0 profile."""

    def describe(self, image: bytes, schema: Mapping[str, Any]) -> dict[str, Any]:
        del image, schema
        raise RuntimeUnavailable("VisionRuntime is disabled on 8 GB (ADR 0001). VLM stays off.")
