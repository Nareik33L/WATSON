"""Inference adapters (plan D6). Ollama for V1.0 dev; fake for CI."""

from watson.intelligence.runtime.fake import FakeRuntime
from watson.intelligence.runtime.http import NoRedirectHandler
from watson.intelligence.runtime.ollama import OllamaRuntime
from watson.intelligence.runtime.select import (
    UNDERSTAND_JOB_KIND,
    RuntimeBundle,
    RuntimeHold,
    build_runtime,
    hold_if_unavailable,
    hold_understanding_jobs,
)
from watson.intelligence.runtime.types import (
    DisabledVisionRuntime,
    EmbeddingRuntime,
    JobFailure,
    LanguageRuntime,
    Message,
    OutputParseError,
    RuntimeCallError,
    RuntimeUnavailable,
    StructuredOutputError,
    VisionRuntime,
    end_runtime_phase,
    format_job_error,
)

__all__ = [
    "UNDERSTAND_JOB_KIND",
    "DisabledVisionRuntime",
    "EmbeddingRuntime",
    "FakeRuntime",
    "JobFailure",
    "LanguageRuntime",
    "Message",
    "NoRedirectHandler",
    "OllamaRuntime",
    "OutputParseError",
    "RuntimeBundle",
    "RuntimeCallError",
    "RuntimeHold",
    "RuntimeUnavailable",
    "StructuredOutputError",
    "format_job_error",
    "VisionRuntime",
    "build_runtime",
    "end_runtime_phase",
    "hold_if_unavailable",
    "hold_understanding_jobs",
]
