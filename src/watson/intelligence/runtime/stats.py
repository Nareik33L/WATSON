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
    consolidate_s: list[float] = field(default_factory=list)
    prompt_chars: list[int] = field(default_factory=list)
    prompt_tokens_est: list[int] = field(default_factory=list)
    timeouts: int = 0

    def summary(self) -> dict[str, Any]:
        chars = [float(item) for item in self.prompt_chars]
        tokens = [float(item) for item in self.prompt_tokens_est]
        return {
            "complete": _latency_block(self.complete_s),
            "embed": _latency_block(self.embed_s),
            "understand_segment": {
                **_latency_block(self.understand_segment_s),
                "per_segment_s": list(self.understand_segment_s),
            },
            "consolidate": {
                **_latency_block(self.consolidate_s),
                "per_day_s": list(self.consolidate_s),
                "total_s": round(sum(self.consolidate_s), 6) if self.consolidate_s else 0.0,
            },
            "prompt": {
                "n": len(self.prompt_chars),
                "chars": list(self.prompt_chars),
                "tokens_est": list(self.prompt_tokens_est),
                "chars_p50": _percentile(chars, 50) if chars else None,
                "chars_p95": _percentile(chars, 95) if chars else None,
                "chars_max": max(self.prompt_chars) if self.prompt_chars else None,
                "tokens_est_p50": _percentile(tokens, 50) if tokens else None,
                "tokens_est_p95": _percentile(tokens, 95) if tokens else None,
                "tokens_est_max": max(self.prompt_tokens_est) if self.prompt_tokens_est else None,
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


def record_understand_prompt(chars: int) -> None:
    stats = _STATS.get()
    if stats is None:
        return
    size = max(0, int(chars))
    stats.prompt_chars.append(size)
    stats.prompt_tokens_est.append((size + 3) // 4)


def record_consolidate(latency_s: float) -> None:
    stats = _STATS.get()
    if stats is not None:
        stats.consolidate_s.append(latency_s)


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

    def complete(
        self,
        messages: Sequence[Message],
        schema: Mapping[str, Any],
        *,
        num_predict: int | None = None,
    ) -> dict[str, Any]:
        started = time.perf_counter()
        try:
            try:
                result: dict[str, Any] = self._inner.complete(
                    messages, schema, num_predict=num_predict
                )
            except TypeError as exc:
                if "num_predict" not in str(exc):
                    raise
                result = self._inner.complete(messages, schema)
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
