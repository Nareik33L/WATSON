"""Optional inference latency collector used by ``watson eval``."""

from __future__ import annotations

import time
from collections.abc import Mapping, Sequence
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from typing import Any

from watson.intelligence.runtime.types import Message, RuntimeCallError


@dataclass
class InferenceStats:
    complete_s: list[float] = field(default_factory=list)
    embed_s: list[float] = field(default_factory=list)
    understand_segment_s: list[float] = field(default_factory=list)
    timeouts: int = 0

    def summary(self) -> dict[str, Any]:
        return {
            "complete": _latency_block(self.complete_s),
            "embed": _latency_block(self.embed_s),
            "understand_segment": {
                **_latency_block(self.understand_segment_s),
                "per_segment_s": list(self.understand_segment_s),
            },
            "timeouts": self.timeouts,
        }


_STATS: ContextVar[InferenceStats | None] = ContextVar("watson_inference_stats", default=None)


def bind_stats(stats: InferenceStats) -> Token[InferenceStats | None]:
    return _STATS.set(stats)


def reset_stats(token: Token[InferenceStats | None]) -> None:
    _STATS.reset(token)


def current_stats() -> InferenceStats | None:
    return _STATS.get()


def record_understand_segment(latency_s: float) -> None:
    stats = _STATS.get()
    if stats is not None:
        stats.understand_segment_s.append(latency_s)


def _is_timeout(exc: BaseException) -> bool:
    if isinstance(exc, TimeoutError):
        return True
    if isinstance(exc, RuntimeCallError) and "timed out" in str(exc).lower():
        return True
    return False


class TimingRuntime:
    """Wrap complete/embed to record per-call latency and timeout counts."""

    def __init__(self, inner: Any, stats: InferenceStats) -> None:
        self._inner = inner
        self._stats = stats
        self.name = str(getattr(inner, "name", "unknown"))

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    def end_phase(self) -> None:
        end = getattr(self._inner, "end_phase", None)
        if callable(end):
            end()

    def complete(self, messages: Sequence[Message], schema: Mapping[str, Any]) -> dict[str, Any]:
        started = time.perf_counter()
        try:
            result: dict[str, Any] = self._inner.complete(messages, schema)
            return result
        except Exception as exc:
            if _is_timeout(exc):
                self._stats.timeouts += 1
            raise
        finally:
            self._stats.complete_s.append(time.perf_counter() - started)

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        started = time.perf_counter()
        try:
            result: list[list[float]] = self._inner.embed(texts)
            return result
        except Exception as exc:
            if _is_timeout(exc):
                self._stats.timeouts += 1
            raise
        finally:
            self._stats.embed_s.append(time.perf_counter() - started)


def _latency_block(values: list[float]) -> dict[str, Any]:
    if not values:
        return {"n": 0, "p50_s": None, "p95_s": None, "max_s": None}
    return {
        "n": len(values),
        "p50_s": _percentile(values, 50),
        "p95_s": _percentile(values, 95),
        "max_s": max(values),
    }


def _percentile(values: list[float], percent: float) -> float:
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * (percent / 100.0)
    low = int(rank)
    high = min(low + 1, len(ordered) - 1)
    frac = rank - low
    return ordered[low] * (1.0 - frac) + ordered[high] * frac
