"""§11.4 eval profile selection and latency/memory boundaries."""

from __future__ import annotations

from watson.intelligence.eval.run import DayEval, EvalReport
from watson.intelligence.eval.thresholds import (
    MIN_SPEC_RAM_LIMIT_BYTES,
    gate_metrics,
    gate_profile,
    load_thresholds,
    select_eval_profile,
)

GIB = 1024**3
THREE_GB = 3 * GIB
THREE_FIVE_GB = int(3.5 * GIB)


def _metrics() -> dict[str, float]:
    metrics = {name: 1.0 for name in load_thresholds()}
    metrics["unsupported_achievements"] = 0.0
    metrics["duplicate_observation_rate"] = 0.0
    return metrics


def _day(metrics: dict[str, float]) -> DayEval:
    return DayEval(
        name="day_acme_q4",
        date="2026-10-31",
        metrics=metrics,
        derived_first={"summaries": 1},
        derived_second={"summaries": 1},
        summary_present=True,
        identical_counts=True,
        narrative_nonempty=True,
        citation_count=1,
        claims=1,
    )


def test_load_thresholds_skips_profiles() -> None:
    rules = load_thresholds()
    assert "profiles" not in rules
    assert rules["decisions_precision"].minimum == 0.85
    assert rules["commitments_precision"].minimum == 0.85


def test_selects_min_spec_below_16_gib() -> None:
    assert select_eval_profile(ram_bytes=8 * GIB) == "min-spec"
    assert select_eval_profile(ram_bytes=8 * GIB - 1) == "min-spec"
    assert select_eval_profile(ram_bytes=MIN_SPEC_RAM_LIMIT_BYTES - 1) == "min-spec"
    assert select_eval_profile(ram_bytes=0) == "min-spec"


def test_selects_primary_at_16_gib_and_above() -> None:
    assert select_eval_profile(ram_bytes=MIN_SPEC_RAM_LIMIT_BYTES) == "primary"
    assert select_eval_profile(ram_bytes=16 * GIB) == "primary"
    assert select_eval_profile(ram_bytes=32 * GIB) == "primary"


def test_profile_override_wins_over_ram() -> None:
    assert select_eval_profile("primary", ram_bytes=8 * GIB) == "primary"
    assert select_eval_profile("min-spec", ram_bytes=32 * GIB) == "min-spec"


def test_min_spec_p95_boundary() -> None:
    under = gate_profile(
        understand_p95_s=34.999,
        worker_plus_model_peak_bytes=THREE_FIVE_GB - 1,
        profile="min-spec",
    )
    at = gate_profile(
        understand_p95_s=35.0,
        worker_plus_model_peak_bytes=THREE_FIVE_GB - 1,
        profile="min-spec",
    )
    assert under["passed"] is True
    assert at["passed"] is False
    assert "understand_p95_s" in at["failures"]


def test_min_spec_peak_memory_boundary() -> None:
    under = gate_profile(
        understand_p95_s=10.0,
        worker_plus_model_peak_bytes=THREE_FIVE_GB - 1,
        profile="min-spec",
    )
    at = gate_profile(
        understand_p95_s=10.0,
        worker_plus_model_peak_bytes=THREE_FIVE_GB,
        profile="min-spec",
    )
    assert under["passed"] is True
    assert at["passed"] is False
    assert "worker_plus_model_peak_bytes" in at["failures"]


def test_primary_p95_boundary() -> None:
    under = gate_profile(
        understand_p95_s=19.999,
        worker_plus_model_peak_bytes=THREE_GB - 1,
        profile="primary",
    )
    at = gate_profile(
        understand_p95_s=20.0,
        worker_plus_model_peak_bytes=THREE_GB - 1,
        profile="primary",
    )
    assert under["passed"] is True
    assert at["passed"] is False
    assert "understand_p95_s" in at["failures"]


def test_primary_peak_memory_boundary() -> None:
    under = gate_profile(
        understand_p95_s=10.0,
        worker_plus_model_peak_bytes=THREE_GB - 1,
        profile="primary",
    )
    at = gate_profile(
        understand_p95_s=10.0,
        worker_plus_model_peak_bytes=THREE_GB,
        profile="primary",
    )
    assert under["passed"] is True
    assert at["passed"] is False
    assert "worker_plus_model_peak_bytes" in at["failures"]


def test_strict_measure_model_uses_profile_gate() -> None:
    metrics = _metrics()
    day = _day(metrics)
    profile_gate = gate_profile(
        understand_p95_s=35.0,
        worker_plus_model_peak_bytes=THREE_FIVE_GB - 1,
        profile="min-spec",
    )
    report = EvalReport(
        mode="record",
        runtime="ollama",
        days=[day],
        aggregate=metrics,
        gate=gate_metrics(metrics),
        profile="min-spec",
        profile_gate=profile_gate,
        measure_model=True,
    )
    assert report.pipeline_passed is True
    assert report.passed is False
    skipped = EvalReport(
        mode="replay",
        runtime="replay",
        days=[day],
        aggregate=metrics,
        gate=gate_metrics(metrics),
        profile="min-spec",
        profile_gate=profile_gate,
        measure_model=False,
    )
    assert skipped.passed is True
