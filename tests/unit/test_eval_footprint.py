"""Offline phys_footprint parsing and sampler (no live ollama)."""

from __future__ import annotations

from watson.intelligence.eval.footprint import (
    FootprintSampler,
    parse_phys_footprint,
)


def test_parse_phys_footprint_units() -> None:
    assert parse_phys_footprint("phys_footprint:  4.19G") == int(4.19 * 1024**3)
    assert parse_phys_footprint("phys_footprint                 1234K") == 1234 * 1024
    assert parse_phys_footprint("phys_footprint: 4194304") == 4194304
    assert parse_phys_footprint("no such field") is None


def test_sampler_adds_injected_pid(monkeypatch: object) -> None:
    monkeypatch.setattr(
        "watson.intelligence.eval.footprint.pid_phys_footprint_bytes",
        lambda pid: 1000 if pid == 99 else 0,
    )
    monkeypatch.setattr("watson.intelligence.eval.footprint.worker_rss_bytes", lambda: 50)
    sampler = FootprintSampler(extra_pids=[99], measure_model=False)
    assert sampler.sample() == 1050


def test_measure_model_includes_discovered_pids(monkeypatch: object) -> None:
    monkeypatch.setattr("watson.intelligence.eval.footprint.find_model_pids", lambda: [7, 8])
    monkeypatch.setattr(
        "watson.intelligence.eval.footprint.pid_phys_footprint_bytes",
        lambda pid: {7: 100, 8: 200}.get(pid, 0),
    )
    monkeypatch.setattr("watson.intelligence.eval.footprint.worker_rss_bytes", lambda: 10)
    sampler = FootprintSampler(extra_pids=[], measure_model=True)
    assert sampler.sample() == 310
