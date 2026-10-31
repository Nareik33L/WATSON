"""Bench-only runtime adapters (D6). Production adapters land at M3."""

from __future__ import annotations

from watson_bench.adapters.base import (
    AdapterError,
    BenchRuntime,
    RuntimeCall,
    Skipped,
    get_runtime,
)
from watson_bench.adapters.fake import FakeRuntime
from watson_bench.adapters.llamacpp import LlamaCppRuntime
from watson_bench.adapters.mlx import MlxRuntime
from watson_bench.adapters.ollama import OllamaRuntime

__all__ = [
    "AdapterError",
    "BenchRuntime",
    "FakeRuntime",
    "LlamaCppRuntime",
    "MlxRuntime",
    "OllamaRuntime",
    "RuntimeCall",
    "Skipped",
    "get_runtime",
]
