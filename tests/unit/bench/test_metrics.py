from __future__ import annotations

from watson_bench.metrics import (
    CallMetrics,
    RssSampler,
    current_rss_bytes,
    estimate_tokens,
    mean,
    tokens_per_second,
)


def test_tokens_per_second_and_mean() -> None:
    assert tokens_per_second(100, 2.0) == 50.0
    assert tokens_per_second(0, 2.0) is None
    assert mean([1.0, 3.0]) == 2.0
    assert mean([]) is None
    assert estimate_tokens("one two three") == 3
    assert CallMetrics(latency_s=2.0, completion_tokens=40).tokens_per_s == 20.0


def test_rss_sampler_records_a_peak() -> None:
    before = current_rss_bytes()
    sampler = RssSampler(interval_s=0.05)
    sampler.start()
    blob = bytearray(1024 * 256)
    peak = sampler.stop()
    assert peak >= before
    del blob
