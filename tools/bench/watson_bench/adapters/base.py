"""Shared adapter protocol for the M0-A bake-off."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from watson_bench.candidates import Candidate
from watson_bench.frames import Frame
from watson_bench.metrics import CallMetrics


class AdapterError(RuntimeError):
    """A loaded runtime failed while benchmarking."""


@dataclass
class Skipped:
    reason: str


@dataclass
class RuntimeCall:
    output: dict[str, Any] | list[list[float]]
    metrics: CallMetrics
    raw_text: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


class BenchRuntime(Protocol):
    name: str

    def availability(self, candidate: Candidate, models_dir: Path) -> Skipped | None:
        """Return a skip reason, or None if this candidate can run."""

    def load(self, candidate: Candidate, models_dir: Path) -> None: ...

    def describe(self, candidate: Candidate, frame: Frame) -> RuntimeCall: ...

    def complete(self, candidate: Candidate, frame: Frame) -> RuntimeCall: ...

    def embed(self, candidate: Candidate, texts: list[str]) -> RuntimeCall: ...

    def unload(self) -> None: ...


def get_runtime(name: str) -> BenchRuntime:
    from watson_bench.adapters.fake import FakeRuntime
    from watson_bench.adapters.llamacpp import LlamaCppRuntime
    from watson_bench.adapters.mlx import MlxRuntime
    from watson_bench.adapters.ollama import OllamaRuntime

    mapping: dict[str, type[BenchRuntime]] = {
        "fake": FakeRuntime,
        "ollama": OllamaRuntime,
        "llamacpp": LlamaCppRuntime,
        "mlx": MlxRuntime,
    }
    try:
        return mapping[name]()
    except KeyError as exc:
        raise AdapterError(f"unknown runtime {name!r}") from exc


def model_dir_for(candidate: Candidate, models_dir: Path) -> Path:
    return models_dir / candidate.runtime / candidate.id
