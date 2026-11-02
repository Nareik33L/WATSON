"""Offline phys_footprint parsing and sampler (no live ollama)."""

from __future__ import annotations

import io
import os
import time
from types import SimpleNamespace

from watson.intelligence.eval import footprint as footprint_mod
from watson.intelligence.eval.footprint import (
    FootprintSampler,
    find_model_pids,
    parse_phys_footprint,
    pid_phys_footprint_bytes,
)


def test_parse_phys_footprint_units() -> None:
    assert parse_phys_footprint("phys_footprint:  4.19G") == int(4.19 * 1024**3)
    assert parse_phys_footprint("phys_footprint                 1234K") == 1234 * 1024
    assert parse_phys_footprint("phys_footprint: 4194304") == 4194304
    assert parse_phys_footprint("no such field") is None


def test_parse_phys_footprint_ignores_peak_alias() -> None:
    """F4: phys_footprint_peak must not match."""
    assert parse_phys_footprint("phys_footprint_peak: 9.99G") is None
    assert parse_phys_footprint("phys_footprint_peak: 9.99G\nphys_footprint: 1.5G") == int(
        1.5 * 1024**3
    )


def test_sampler_adds_injected_pid(monkeypatch: object) -> None:
    monkeypatch.setattr(
        "watson.intelligence.eval.footprint.pid_phys_footprint_bytes",
        lambda pid: 1000 if pid == 99 else 0,
    )
    monkeypatch.setattr("watson.intelligence.eval.footprint.worker_rss_bytes", lambda: 50)
    sampler = FootprintSampler(extra_pids=[99], measure_model=False)
    assert sampler.sample() == 1050
    assert sampler.worker_peak_bytes == 50


def test_measure_model_includes_discovered_pids(monkeypatch: object) -> None:
    monkeypatch.setattr("watson.intelligence.eval.footprint.find_model_pids", lambda: [7, 8])
    monkeypatch.setattr(
        "watson.intelligence.eval.footprint.pid_phys_footprint_bytes",
        lambda pid: {7: 100, 8: 200}.get(pid, 0),
    )
    monkeypatch.setattr("watson.intelligence.eval.footprint.worker_rss_bytes", lambda: 10)
    sampler = FootprintSampler(extra_pids=[], measure_model=True)
    assert sampler.sample() == 310


def test_zero_darwin_footprint_falls_through_to_linux(monkeypatch: object) -> None:
    """F1: a PID that returns 0 is missing, not a real measurement."""
    monkeypatch.setattr(footprint_mod.sys, "platform", "darwin")
    monkeypatch.setattr(footprint_mod, "_darwin_footprint", lambda pid: 0)
    monkeypatch.setattr(footprint_mod, "_linux_vmrss", lambda pid: 4096)
    assert pid_phys_footprint_bytes(11) == 4096


def test_linux_vmrss_multiplies_kb_unit(monkeypatch: object) -> None:
    """F5: VmRSS is kB."""

    def fake_open(path: str, *args: object, **kwargs: object) -> io.StringIO:
        del args, kwargs
        assert path.endswith("/status")
        return io.StringIO("Name:\tpython\nVmRSS:\t2048 kB\n")

    monkeypatch.setattr(footprint_mod.sys, "platform", "linux")
    monkeypatch.setattr("builtins.open", fake_open)
    assert pid_phys_footprint_bytes(22) == 2048 * 1024


def test_sampler_loop_samples_between_start_and_stop(monkeypatch: object) -> None:
    """F7: the sampler actually samples in the background loop."""
    calls = {"n": 0}

    def fake_sample(self: FootprintSampler) -> int:
        del self
        calls["n"] += 1
        return calls["n"] * 10

    monkeypatch.setattr(FootprintSampler, "sample", fake_sample)
    sampler = FootprintSampler(interval_s=0.02)
    sampler.start()
    deadline = time.monotonic() + 1.0
    while calls["n"] < 3 and time.monotonic() < deadline:
        time.sleep(0.02)
    peak = sampler.stop()
    assert calls["n"] >= 3
    assert peak >= 30


def test_stop_returns_recorded_peak_not_zero(monkeypatch: object) -> None:
    """F8: stop() returns the peak, not 0."""
    monkeypatch.setattr(FootprintSampler, "sample", lambda self: 0)
    sampler = FootprintSampler()
    sampler.peak_bytes = 999
    assert sampler.stop() == 999


def test_dead_pid_does_not_raise(monkeypatch: object) -> None:
    """F9: a dead PID returns 0."""

    def boom(*_a: object, **_k: object) -> int:
        raise OSError("no such process")

    monkeypatch.setattr(footprint_mod, "_darwin_footprint", boom)
    monkeypatch.setattr(footprint_mod, "_linux_vmrss", boom)
    monkeypatch.setattr(footprint_mod, "_ps_rss", boom)
    assert pid_phys_footprint_bytes(2_147_483_647) == 0


def test_macos_path_is_used_when_platform_is_darwin(monkeypatch: object) -> None:
    """F10: Darwin phys_footprint is not skipped."""
    monkeypatch.setattr(footprint_mod.sys, "platform", "darwin")
    monkeypatch.setattr(footprint_mod, "_darwin_footprint", lambda pid: 12345)
    monkeypatch.setattr(footprint_mod, "_linux_vmrss", lambda pid: 1)
    monkeypatch.setattr(footprint_mod, "_ps_rss", lambda pid: 2)
    assert pid_phys_footprint_bytes(33) == 12345


def test_pgrep_excludes_self_and_does_not_use_minus_f(monkeypatch: object) -> None:
    """F11: pgrep -f ollama must not count the watson eval wrapper / this PID."""
    self_pid = os.getpid()
    captured: list[list[str]] = []

    def fake_run(argv: list[str], **_kwargs: object) -> SimpleNamespace:
        captured.append(list(argv))
        return SimpleNamespace(stdout=f"{self_pid}\n4242\n", returncode=0)

    monkeypatch.setattr(footprint_mod.subprocess, "run", fake_run)
    monkeypatch.setattr(footprint_mod, "_is_watson_wrapper", lambda pid: pid == self_pid)
    found = find_model_pids()
    assert 4242 in found
    assert self_pid not in found
    ollama = next(argv for argv in captured if argv[-1] == "ollama")
    assert ollama[0] == "pgrep"
    assert "-x" in ollama
    assert "-f" not in ollama
