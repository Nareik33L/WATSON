"""Timing, throughput, and RSS helpers for §11.4 / M0-A metrics."""

from __future__ import annotations

import os
import resource
import sys
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class CallMetrics:
    latency_s: float
    completion_tokens: int = 0
    prompt_tokens: int = 0

    @property
    def tokens_per_s(self) -> float | None:
        return tokens_per_second(self.completion_tokens, self.latency_s)


@dataclass
class RoleMetrics:
    load_time_s: float | None = None
    peak_rss_bytes: int | None = None
    tokens_per_s: float | None = None
    time_per_frame_s: float | None = None
    time_per_call_s: float | None = None
    embedding_texts_per_s: float | None = None
    n_calls: int = 0
    completion_tokens: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RssSampler:
    """Background peak-RSS sampler for this process (and optional extra PIDs)."""

    interval_s: float = 0.2
    extra_pids: list[int] = field(default_factory=list)
    peak_bytes: int = 0
    _stop: threading.Event = field(default_factory=threading.Event, init=False)
    _thread: threading.Thread | None = field(default=None, init=False)

    def start(self) -> None:
        self.peak_bytes = max(self.peak_bytes, current_rss_bytes(self.extra_pids))
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="rss-sampler", daemon=True)
        self._thread.start()

    def stop(self) -> int:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
        sampled = current_rss_bytes(self.extra_pids)
        self.peak_bytes = max(self.peak_bytes, sampled, rusage_peak_rss())
        return self.peak_bytes

    def _loop(self) -> None:
        while not self._stop.wait(self.interval_s):
            self.peak_bytes = max(self.peak_bytes, current_rss_bytes(self.extra_pids))


def tokens_per_second(completion_tokens: int, latency_s: float) -> float | None:
    if completion_tokens <= 0 or latency_s <= 0:
        return None
    return completion_tokens / latency_s


def estimate_tokens(text: str) -> int:
    words = [part for part in text.split() if part]
    return max(len(words), 1) if text.strip() else 0


def mean(values: list[float]) -> float | None:
    if not values:
        return None
    return sum(values) / len(values)


def current_rss_bytes(extra_pids: list[int] | None = None) -> int:
    total = _self_rss_bytes()
    for pid in extra_pids or []:
        total += _pid_rss_bytes(pid)
    return total


def rusage_peak_rss() -> int:
    usage = resource.getrusage(resource.RUSAGE_SELF)
    rss = int(usage.ru_maxrss)
    if sys.platform == "darwin":
        return rss
    return rss * 1024


def _self_rss_bytes() -> int:
    status = _read_vm_rss("/proc/self/status")
    if status is not None:
        return status
    return rusage_peak_rss()


def _pid_rss_bytes(pid: int) -> int:
    status = _read_vm_rss(f"/proc/{pid}/status")
    if status is not None:
        return status
    try:
        import subprocess

        proc = subprocess.run(
            ["ps", "-o", "rss=", "-p", str(pid)],
            check=False,
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.SubprocessError):
        return 0
    text = proc.stdout.strip()
    if not text:
        return 0
    try:
        return int(text.split()[0]) * 1024
    except ValueError:
        return 0


def _read_vm_rss(path: str) -> int | None:
    try:
        with open(path, encoding="utf-8") as handle:
            for line in handle:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) * 1024
    except (OSError, ValueError):
        return None
    return None


@contextmanager
def timed() -> Iterator[Callable[[], float]]:
    start = time.perf_counter()
    elapsed = lambda: time.perf_counter() - start  # noqa: E731
    yield elapsed


def find_process_pids(name: str) -> list[int]:
    try:
        import subprocess

        proc = subprocess.run(
            ["pgrep", "-f", name],
            check=False,
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    pids: list[int] = []
    self_pid = os.getpid()
    for line in proc.stdout.split():
        try:
            pid = int(line)
        except ValueError:
            continue
        if pid != self_pid:
            pids.append(pid)
    return pids
