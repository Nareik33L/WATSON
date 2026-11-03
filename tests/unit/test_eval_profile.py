"""§11.4 eval profile selection and latency/memory boundaries."""

from __future__ import annotations

import pytest

from watson.intelligence.eval.run import DayEval, EvalReport, _completed_without_kill
from watson.intelligence.eval.thresholds import (
    HEADROOM_PEAK_BYTES,
    HEADROOM_UNDERSTAND_P95_S,
    MIN_SPEC_RAM_LIMIT_BYTES,
    gate_metrics,
    gate_profile,
    headroom_status,
    load_profile_thresholds,
    load_thresholds,
    select_eval_profile,
)

GIB = 1024**3
THREE_GB = 3 * GIB
THREE_FIVE_GB = int(3.5 * GIB)
THREE_THREE_GB = int(3.3 * GIB)
OK_CONSOLIDATE_S = 1.0


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


def test_min_spec_records_p95_and_peak_without_gating() -> None:
    """Min-spec p95 / peak are reported, not gated (Kieran 21:53)."""
    slow = gate_profile(
        understand_p95_s=120.0,
        worker_plus_model_peak_bytes=8 * GIB,
        consolidate_s=OK_CONSOLIDATE_S,
        completed_without_kill=1.0,
        profile="min-spec",
    )
    assert slow["passed"] is True
    assert slow["failures"] == []
    assert "understand_p95_s" not in load_profile_thresholds()["min-spec"]
    assert "worker_plus_model_peak_bytes" not in load_profile_thresholds()["min-spec"]


def test_min_spec_completed_without_kill_gate() -> None:
    ok = gate_profile(
        understand_p95_s=40.0,
        worker_plus_model_peak_bytes=8 * GIB,
        consolidate_s=OK_CONSOLIDATE_S,
        completed_without_kill=1.0,
        profile="min-spec",
    )
    dead = gate_profile(
        understand_p95_s=10.0,
        worker_plus_model_peak_bytes=THREE_FIVE_GB - 1,
        consolidate_s=OK_CONSOLIDATE_S,
        completed_without_kill=0.0,
        profile="min-spec",
    )
    missing = gate_profile(
        understand_p95_s=10.0,
        worker_plus_model_peak_bytes=THREE_FIVE_GB - 1,
        consolidate_s=OK_CONSOLIDATE_S,
        profile="min-spec",
    )
    assert ok["passed"] is True
    assert dead["passed"] is False
    assert "completed_without_kill" in dead["failures"]
    assert missing["passed"] is False
    assert "completed_without_kill" in missing["failures"]


def test_primary_p95_boundary() -> None:
    under = gate_profile(
        understand_p95_s=19.999,
        worker_plus_model_peak_bytes=THREE_GB - 1,
        consolidate_s=OK_CONSOLIDATE_S,
        completed_without_kill=1.0,
        profile="primary",
    )
    at = gate_profile(
        understand_p95_s=20.0,
        worker_plus_model_peak_bytes=THREE_GB - 1,
        consolidate_s=OK_CONSOLIDATE_S,
        completed_without_kill=1.0,
        profile="primary",
    )
    assert under["passed"] is True
    assert at["passed"] is False
    assert "understand_p95_s" in at["failures"]


def test_primary_peak_memory_boundary() -> None:
    under = gate_profile(
        understand_p95_s=10.0,
        worker_plus_model_peak_bytes=THREE_GB - 1,
        consolidate_s=OK_CONSOLIDATE_S,
        completed_without_kill=1.0,
        profile="primary",
    )
    at = gate_profile(
        understand_p95_s=10.0,
        worker_plus_model_peak_bytes=THREE_GB,
        consolidate_s=OK_CONSOLIDATE_S,
        completed_without_kill=1.0,
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
        consolidate_s=OK_CONSOLIDATE_S,
        completed_without_kill=0.0,
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


def _ok_peak(profile: str) -> int:
    return (THREE_GB if profile == "primary" else THREE_FIVE_GB) - 1


def test_profile_gate_fails_closed_on_missing_p95() -> None:
    """Primary still fail-closes on missing understand p95. Min-spec does not gate it."""
    none = gate_profile(
        understand_p95_s=None,
        worker_plus_model_peak_bytes=_ok_peak("primary"),
        consolidate_s=OK_CONSOLIDATE_S,
        completed_without_kill=1.0,
        profile="primary",
    )
    assert none["passed"] is False
    assert "understand_p95_s" in none["failures"]
    min_spec = gate_profile(
        understand_p95_s=None,
        worker_plus_model_peak_bytes=_ok_peak("min-spec"),
        consolidate_s=OK_CONSOLIDATE_S,
        completed_without_kill=1.0,
        profile="min-spec",
    )
    assert min_spec["passed"] is True


def test_profile_gate_fails_closed_on_missing_peak_memory() -> None:
    """Primary still fail-closes on missing worker+model peak. Min-spec does not gate it."""
    none = gate_profile(
        understand_p95_s=10.0,
        worker_plus_model_peak_bytes=None,
        consolidate_s=OK_CONSOLIDATE_S,
        completed_without_kill=1.0,
        profile="primary",
    )
    assert none["passed"] is False
    assert "worker_plus_model_peak_bytes" in none["failures"]
    min_spec = gate_profile(
        understand_p95_s=10.0,
        worker_plus_model_peak_bytes=None,
        consolidate_s=OK_CONSOLIDATE_S,
        completed_without_kill=1.0,
        profile="min-spec",
    )
    assert min_spec["passed"] is True


@pytest.mark.parametrize("profile", ["primary", "min-spec"])
def test_strict_measure_model_fails_closed_on_missing_profile_measurements(
    profile: str,
) -> None:
    """PR5: --strict --measure-model with None p95 and peak fails EvalReport.passed."""
    metrics = _metrics()
    day = _day(metrics)
    profile_gate = gate_profile(
        understand_p95_s=None,
        worker_plus_model_peak_bytes=None,
        profile=profile,
    )
    report = EvalReport(
        mode="record",
        runtime="ollama",
        days=[day],
        aggregate=metrics,
        gate=gate_metrics(metrics),
        profile=profile,
        profile_gate=profile_gate,
        measure_model=True,
    )
    assert profile_gate["passed"] is False
    if profile == "primary":
        assert "understand_p95_s" in profile_gate["failures"]
        assert "worker_plus_model_peak_bytes" in profile_gate["failures"]
    else:
        assert "completed_without_kill" in profile_gate["failures"]
        assert "understand_p95_s" not in profile_gate["failures"]
        assert "worker_plus_model_peak_bytes" not in profile_gate["failures"]
    assert report.pipeline_passed is True
    assert report.passed is False


def test_strict_measure_model_fails_closed_on_missing_profile_gate() -> None:
    """PR5: an absent profile_gate under --measure-model must not pass."""
    metrics = _metrics()
    report = EvalReport(
        mode="record",
        runtime="ollama",
        days=[_day(metrics)],
        aggregate=metrics,
        gate=gate_metrics(metrics),
        profile="min-spec",
        profile_gate={},
        measure_model=True,
    )
    assert report.pipeline_passed is True
    assert report.passed is False


def test_profile_peak_caps_are_gib() -> None:
    """Plan '3 GB' is 3 GiB in thresholds.json, not decimal GB. Min-spec does not cap peak."""
    profiles = load_profile_thresholds()
    assert profiles["primary"]["worker_plus_model_peak_bytes"].maximum_exclusive == THREE_GB
    assert "worker_plus_model_peak_bytes" not in profiles["min-spec"]
    assert profiles["min-spec"]["completed_without_kill"].minimum == 1.0
    assert "consolidate_s" not in profiles["min-spec"]
    assert profiles["primary"]["consolidate_s"].maximum_exclusive == 900
    assert THREE_GB == 3 * 1024**3
    assert THREE_GB != 3 * 1000**3
    assert THREE_FIVE_GB == 3758096384
    assert THREE_THREE_GB == 3543348019
    assert HEADROOM_PEAK_BYTES == THREE_THREE_GB
    assert HEADROOM_UNDERSTAND_P95_S == 30.0


def test_unmeasured_consolidation_cap_fails_closed_on_primary_only() -> None:
    """Primary still fail-closes on missing consolidate_s. Min-spec records it."""
    missing_primary = gate_profile(
        understand_p95_s=10.0,
        worker_plus_model_peak_bytes=_ok_peak("primary"),
        completed_without_kill=1.0,
        profile="primary",
    )
    assert missing_primary["passed"] is False
    assert "consolidate_s" in missing_primary["failures"]
    missing_min = gate_profile(
        understand_p95_s=10.0,
        worker_plus_model_peak_bytes=_ok_peak("min-spec"),
        completed_without_kill=1.0,
        profile="min-spec",
    )
    assert missing_min["passed"] is True
    assert "consolidate_s" not in missing_min["failures"]


def test_min_spec_passes_slow_consolidation_and_fails_on_kill() -> None:
    """Kieran 02:59: min-spec records consolidate_s; gate is completed_without_kill."""
    slow = gate_profile(
        understand_p95_s=10.0,
        worker_plus_model_peak_bytes=_ok_peak("min-spec"),
        consolidate_s=10_000.0,
        completed_without_kill=1.0,
        profile="min-spec",
    )
    dead = gate_profile(
        understand_p95_s=10.0,
        worker_plus_model_peak_bytes=_ok_peak("min-spec"),
        consolidate_s=10_000.0,
        completed_without_kill=0.0,
        profile="min-spec",
    )
    assert slow["passed"] is True
    assert "consolidate_s" not in slow["failures"]
    assert "consolidate_s" not in load_profile_thresholds()["min-spec"]
    assert dead["passed"] is False
    assert "completed_without_kill" in dead["failures"]


@pytest.mark.parametrize("metric", ["decisions_precision", "commitments_precision"])
def test_strict_min_spec_fails_precision_0_84_and_passes_0_85(metric: str) -> None:
    """§11.2 precision stays a min-spec gate. 0.84 fails; 0.85 passes.

    A mutant that skips decisions/commitments precision for min-spec only
    (EvalReport.passed, or gate_metrics when profile is min-spec) dies here.
    """
    profile_gate = gate_profile(
        understand_p95_s=120.0,
        worker_plus_model_peak_bytes=8 * GIB,
        consolidate_s=10_000.0,
        completed_without_kill=1.0,
        profile="min-spec",
    )
    assert profile_gate["passed"] is True

    def report_for(value: float) -> EvalReport:
        metrics = _metrics()
        metrics[metric] = value
        return EvalReport(
            mode="record",
            runtime="ollama",
            days=[_day(metrics)],
            aggregate=metrics,
            gate=gate_metrics(metrics),
            profile="min-spec",
            profile_gate=profile_gate,
            measure_model=True,
        )

    miss = report_for(0.84)
    hit = report_for(0.85)
    assert miss.profile == "min-spec"
    assert miss.aggregate[metric] == 0.84
    assert miss.gate["passed"] is False
    assert metric in miss.gate["failures"]
    assert miss.pipeline_passed is True
    assert miss.passed is False
    assert hit.aggregate[metric] == 0.85
    assert hit.gate["passed"] is True
    assert metric not in (hit.gate.get("failures") or [])
    assert hit.passed is True


def test_primary_still_fails_on_consolidate_s_at_900() -> None:
    under = gate_profile(
        understand_p95_s=10.0,
        worker_plus_model_peak_bytes=_ok_peak("primary"),
        consolidate_s=899.999,
        completed_without_kill=1.0,
        profile="primary",
    )
    at = gate_profile(
        understand_p95_s=10.0,
        worker_plus_model_peak_bytes=_ok_peak("primary"),
        consolidate_s=900.0,
        completed_without_kill=1.0,
        profile="primary",
    )
    assert under["passed"] is True
    assert at["passed"] is False
    assert "consolidate_s" in at["failures"]


def test_headroom_miss_does_not_fail_min_spec_gate() -> None:
    """30 s / 3.3 GiB is engineering headroom, not the §14 #9 min-spec gate."""
    gate = gate_profile(
        understand_p95_s=34.0,
        worker_plus_model_peak_bytes=THREE_FIVE_GB - 1,
        consolidate_s=OK_CONSOLIDATE_S,
        completed_without_kill=1.0,
        profile="min-spec",
    )
    room = headroom_status(
        understand_p95_s=34.0,
        worker_plus_model_peak_bytes=THREE_FIVE_GB - 1,
    )
    assert gate["passed"] is True
    assert room["gating"] is False
    assert room["understand_p95_s"]["within"] is False
    assert room["worker_plus_model_peak_bytes"]["within"] is False
    metrics = _metrics()
    report = EvalReport(
        mode="record",
        runtime="ollama",
        days=[_day(metrics)],
        aggregate=metrics,
        gate=gate_metrics(metrics),
        profile="min-spec",
        profile_gate=gate,
        headroom=room,
        measure_model=True,
    )
    assert report.passed is True


def test_completed_without_kill_requires_every_fixture_day() -> None:
    day = _day(_metrics())
    assert _completed_without_kill(("day_acme_q4",), [day], failure_reason=None) == 1.0
    assert (
        _completed_without_kill(("day_acme_q4",), [day], failure_reason="RuntimeUnavailable") == 0.0
    )
    failed = _day(_metrics())
    failed.error = "RuntimeCallError"
    assert _completed_without_kill(("day_acme_q4",), [failed], failure_reason=None) == 0.0
    assert _completed_without_kill(("a", "b"), [day], failure_reason=None) == 0.0
    assert _completed_without_kill((), [], failure_reason=None) == 0.0


def test_headroom_within_at_30s_and_3_3_gib() -> None:
    room = headroom_status(
        understand_p95_s=29.999,
        worker_plus_model_peak_bytes=THREE_THREE_GB - 1,
    )
    at = headroom_status(
        understand_p95_s=30.0,
        worker_plus_model_peak_bytes=THREE_THREE_GB,
    )
    assert room["understand_p95_s"]["max_exclusive"] == 30.0
    assert room["worker_plus_model_peak_bytes"]["max_exclusive"] == THREE_THREE_GB
    assert room["understand_p95_s"]["within"] is True
    assert room["worker_plus_model_peak_bytes"]["within"] is True
    assert at["understand_p95_s"]["within"] is False
    assert at["worker_plus_model_peak_bytes"]["within"] is False
