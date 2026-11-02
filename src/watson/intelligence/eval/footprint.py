"""Sample worker + resident-model memory for §11.4.

§11.4 "worker peak RSS" is **worker process + resident model server**,
measured by Darwin ``phys_footprint`` (includes Metal/GPU allocations).
Linux falls back to ``VmRSS``, which does not include GPU memory.
"""

from __future__ import annotations

import os
import re
import resource
import subprocess
import sys
import threading
from dataclasses import dataclass, field

_PHYS = re.compile(
    r"(?<![A-Za-z0-9_])phys_footprint(?![A-Za-z0-9_])[:\s]+([0-9]*\.?[0-9]+)\s*([KMGT]i?B?)?",
    re.IGNORECASE,
)
_MODEL_NAMES = ("ollama", "llama-server", "llama.cpp")
_WATSON_CMDLINE = (b"watson", b"watson-")


def parse_phys_footprint(text: str) -> int | None:
    """Parse ``footprint`` / ``vmmap`` output for a phys_footprint value in bytes."""
    match = _PHYS.search(text)
    if match is None:
        return None
    amount = float(match.group(1))
    unit = (match.group(2) or "").upper().replace("IB", "B")
    multipliers = {"": 1, "B": 1, "K": 1024, "M": 1024**2, "G": 1024**3, "T": 1024**4}
    key = unit[:1] if unit else ""
    return int(amount * multipliers.get(key, 1))


def find_model_pids() -> list[int]:
    """PIDs of Ollama / llama-server processes, excluding this process.

    Match the process name (not ``pgrep -f``) so ``watson eval --runtime ollama``
    is not counted as the model server.
    """
    found: list[int] = []
    self_pid = os.getpid()
    for name in _MODEL_NAMES:
        argv = ["pgrep", "-x", name] if "." not in name else ["pgrep", "-f", name]
        try:
            proc = subprocess.run(
                argv,
                check=False,
                capture_output=True,
                text=True,
                timeout=2,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        for token in proc.stdout.split():
            try:
                pid = int(token)
            except ValueError:
                continue
            if pid == self_pid or pid in found:
                continue
            if _is_watson_wrapper(pid):
                continue
            found.append(pid)
    return found


def pid_phys_footprint_bytes(pid: int) -> int:
    """Darwin phys_footprint via ``footprint``; Linux ``VmRSS``."""
    try:
        if sys.platform == "darwin":
            darwin = _darwin_footprint(pid)
            if darwin:
                return darwin
        linux = _linux_vmrss(pid)
        if linux:
            return linux
        return _ps_rss(pid)
    except (OSError, ValueError, subprocess.SubprocessError):
        return 0


def worker_rss_bytes() -> int:
    linux = _linux_vmrss(os.getpid())
    if linux is not None:
        return linux
    rss = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    if sys.platform == "darwin":
        return rss
    return rss * 1024


def _darwin_footprint(pid: int) -> int | None:
    for argv in (
        ["footprint", "-p", str(pid)],
        ["footprint", str(pid)],
    ):
        try:
            proc = subprocess.run(argv, check=False, capture_output=True, text=True, timeout=5)
        except (OSError, subprocess.SubprocessError):
            continue
        parsed = parse_phys_footprint(proc.stdout + "\n" + proc.stderr)
        if parsed is not None:
            return parsed
    return None


def _linux_vmrss(pid: int) -> int | None:
    try:
        with open(f"/proc/{pid}/status", encoding="utf-8") as handle:
            for line in handle:
                if not line.startswith("VmRSS:"):
                    continue
                parts = line.split()
                if len(parts) < 2:
                    return None
                amount = int(parts[1])
                unit = parts[2].casefold() if len(parts) > 2 else "kb"
                if unit.startswith("kb"):
                    return amount * 1024
                if unit.startswith("mb"):
                    return amount * 1024 * 1024
                if unit.startswith("gb"):
                    return amount * 1024**3
                if unit in {"b", "bytes"}:
                    return amount
                return amount * 1024
    except (OSError, ValueError):
        return None
    return None


def _is_watson_wrapper(pid: int) -> bool:
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as handle:
            cmdline = handle.read().replace(b"\x00", b" ")
    except OSError:
        return False
    return any(token in cmdline for token in _WATSON_CMDLINE)


def _ps_rss(pid: int) -> int:
    try:
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


@dataclass
class FootprintSampler:
    """Background peak of worker RSS + sampled model phys_footprint."""

    extra_pids: list[int] = field(default_factory=list)
    measure_model: bool = False
    interval_s: float = 0.5
    peak_bytes: int = 0
    worker_peak_bytes: int = 0
    _stop: threading.Event = field(default_factory=threading.Event, init=False)
    _thread: threading.Thread | None = field(default=None, init=False)

    def start(self) -> None:
        self.peak_bytes = max(self.peak_bytes, self.sample())
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="footprint-sampler", daemon=True)
        self._thread.start()

    def stop(self) -> int:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
        self.peak_bytes = max(self.peak_bytes, self.sample())
        return self.peak_bytes

    def sample(self) -> int:
        pids = list(self.extra_pids)
        if self.measure_model:
            for pid in find_model_pids():
                if pid not in pids:
                    pids.append(pid)
        worker = worker_rss_bytes()
        self.worker_peak_bytes = max(self.worker_peak_bytes, worker)
        total = worker
        for pid in pids:
            try:
                total += pid_phys_footprint_bytes(pid)
            except (OSError, ValueError, subprocess.SubprocessError):
                continue
        return total

    def _loop(self) -> None:
        while not self._stop.wait(self.interval_s):
            self.peak_bytes = max(self.peak_bytes, self.sample())
