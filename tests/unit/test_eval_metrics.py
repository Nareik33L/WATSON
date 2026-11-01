"""§11.2 metric mutants: precision/recall swap, off-by-one thresholds, empty F1."""

from __future__ import annotations

from watson.intelligence.eval.metrics import (
    completed_accuracy,
    duplicate_rate,
    f1_score,
    precision,
    recall,
    unsupported_count,
)
from watson.intelligence.eval.run import DayEval, EvalReport, _aggregate
from watson.intelligence.eval.thresholds import Threshold, gate_metrics, load_thresholds


def test_empty_sets_score_perfect() -> None:
    assert f1_score([], []) == 1.0
    assert precision([], []) == 1.0
    assert recall([], []) == 1.0
    assert completed_accuracy([], [], []) == 1.0
    assert unsupported_count([], []) == 0


def test_f1_is_not_accuracy() -> None:
    predicted = ["Acme Q4 Proposal", "Ghost Project"]
    truth = ["Acme Q4 Proposal", "Internal Onboarding Guide"]
    assert f1_score(predicted, truth) == 0.5
    assert precision(predicted, truth) == 0.5
    assert recall(predicted, truth) == 0.5


def test_precision_and_recall_are_not_swappable() -> None:
    predicted = ["Agreed to move the Harbor walkthrough to Thursday"]
    truth = [
        "Agreed to move the Harbor walkthrough to Thursday",
        "Use a two-year term for the Northwind renewal",
    ]
    rec = recall(predicted, truth)
    prec = precision(predicted, truth)
    assert rec == 0.5
    assert prec == 1.0
    rules = load_thresholds()
    # rec=0.88, prec=0.72 fails real thresholds (prec >= 0.85) but would pass
    # a swapped gate (rec >= 0.85 and prec >= 0.70).
    swapped = {
        "decisions_recall": 0.88,
        "decisions_precision": 0.72,
        "project_f1": 1.0,
        "people_f1": 1.0,
        "completed_accuracy": 1.0,
        "commitments_recall": 1.0,
        "commitments_precision": 1.0,
        "unsupported_achievements": 0.0,
        "duplicate_observation_rate": 0.0,
        "noise_non_work_rate": 1.0,
        "claims_with_evidence": 1.0,
        "citations_resolvable": 1.0,
    }
    gate = gate_metrics(swapped, rules)
    assert gate["passed"] is False
    assert "decisions_precision" in gate["failures"]
    assert rec < rules["decisions_recall"].minimum or prec >= rules["decisions_precision"].minimum


def test_off_by_one_thresholds_fail() -> None:
    rules = load_thresholds()
    assert rules["project_f1"].allows(0.80) is True
    assert rules["project_f1"].allows(0.799) is False
    assert rules["people_f1"].allows(0.75) is True
    assert rules["people_f1"].allows(0.749) is False
    assert rules["claims_with_evidence"].allows(1.0) is True
    assert rules["claims_with_evidence"].allows(0.99) is False
    assert rules["citations_resolvable"].allows(0.99) is False
    assert rules["unsupported_achievements"].allows(0.0) is True
    assert rules["unsupported_achievements"].allows(1.0) is False
    assert rules["duplicate_observation_rate"].allows(0.049) is True
    assert rules["duplicate_observation_rate"].allows(0.05) is False


def test_hard_metrics_are_named_in_gate() -> None:
    rules = load_thresholds()
    metrics = {name: 1.0 for name in rules}
    metrics["unsupported_achievements"] = 1.0
    metrics["claims_with_evidence"] = 0.99
    metrics["citations_resolvable"] = 0.99
    gate = gate_metrics(metrics, rules)
    assert set(gate["hard_failures"]) == {
        "unsupported_achievements",
        "claims_with_evidence",
        "citations_resolvable",
    }


def test_threshold_minimum_is_not_maximum() -> None:
    rule = Threshold(minimum=0.85)
    assert rule.allows(0.84) is False
    assert rule.allows(0.85) is True
    inverted = Threshold(maximum=0.70)
    assert inverted.allows(0.72) is False


def test_duplicate_rate_counts_extras_not_uniques() -> None:
    assert duplicate_rate(["seg-a", "seg-a", "seg-b"]) == 1 / 3
    assert duplicate_rate(["seg-a", "seg-b", "seg-c"]) == 0.0
    assert duplicate_rate([]) == 0.0


def test_duplicate_rate_is_per_segment_not_whole_day_description() -> None:
    """§11.2 / D3: one observation per segment is not a duplicate.

    Two revisits of the same window are two segments. Same description
    across those ids must not raise the rate. Two rows on one segment must.
    """
    morning, afternoon = "seg-acme-morning", "seg-acme-afternoon"
    assert duplicate_rate([morning, afternoon]) == 0.0
    assert duplicate_rate([morning, morning]) == 0.5
    assert duplicate_rate([morning, morning, afternoon]) == 1 / 3


def test_parenthetical_people_match() -> None:
    assert precision(["Priya Natarajan"], ["Priya Natarajan (Acme)"]) == 1.0
    assert recall(["Priya Natarajan"], ["Priya Natarajan (Acme)"]) == 1.0


def test_commitments_precision_and_recall_are_not_swappable() -> None:
    predicted = ["Send revised pricing to Priya by Friday"]
    truth = [
        "Send revised pricing to Priya by Friday",
        "Flag Q4 delivery risks in the proposal appendix",
    ]
    assert recall(predicted, truth) == 0.5
    assert precision(predicted, truth) == 1.0
    rules = load_thresholds()
    swapped = {
        "commitments_recall": 0.88,
        "commitments_precision": 0.72,
        "decisions_recall": 1.0,
        "decisions_precision": 1.0,
        "project_f1": 1.0,
        "people_f1": 1.0,
        "completed_accuracy": 1.0,
        "unsupported_achievements": 0.0,
        "duplicate_observation_rate": 0.0,
        "noise_non_work_rate": 1.0,
        "claims_with_evidence": 1.0,
        "citations_resolvable": 1.0,
    }
    gate = gate_metrics(swapped, rules)
    assert gate["passed"] is False
    assert "commitments_precision" in gate["failures"]
    assert "commitments_recall" not in gate["failures"]


def test_off_by_one_every_threshold() -> None:
    rules = load_thresholds()
    assert set(rules) == {
        "project_f1",
        "people_f1",
        "completed_accuracy",
        "decisions_recall",
        "decisions_precision",
        "commitments_recall",
        "commitments_precision",
        "unsupported_achievements",
        "duplicate_observation_rate",
        "noise_non_work_rate",
        "claims_with_evidence",
        "citations_resolvable",
    }
    assert rules["completed_accuracy"].allows(0.90) is True
    assert rules["completed_accuracy"].allows(0.899) is False
    assert rules["decisions_recall"].allows(0.70) is True
    assert rules["decisions_recall"].allows(0.699) is False
    assert rules["decisions_precision"].allows(0.85) is True
    assert rules["decisions_precision"].allows(0.849) is False
    assert rules["commitments_recall"].allows(0.70) is True
    assert rules["commitments_recall"].allows(0.699) is False
    assert rules["commitments_precision"].allows(0.85) is True
    assert rules["commitments_precision"].allows(0.849) is False
    assert rules["noise_non_work_rate"].allows(0.90) is True
    assert rules["noise_non_work_rate"].allows(0.899) is False


def _all_metrics(**overrides: float) -> dict[str, float]:
    metrics = {name: 1.0 for name in load_thresholds()}
    metrics["unsupported_achievements"] = 0.0
    metrics["duplicate_observation_rate"] = 0.0
    metrics.update(overrides)
    return metrics


def test_missing_metric_is_a_failure_not_a_pass() -> None:
    rules = load_thresholds()
    metrics = _all_metrics()
    del metrics["project_f1"]
    gate = gate_metrics(metrics, rules)
    assert gate["passed"] is False
    assert "project_f1" in gate["failures"]


def test_empty_aggregate_is_a_failure() -> None:
    gate = gate_metrics({}, load_thresholds())
    assert gate["passed"] is False
    assert "empty_aggregate" in gate["failures"]
    assert gate["hard_failures"]


def test_zero_fixtures_do_not_score_perfect() -> None:
    assert _aggregate([]) == {}
    report = EvalReport(
        mode="replay", runtime="replay", days=[], aggregate={}, gate=gate_metrics({})
    )
    assert report.passed is False
    assert report.pipeline_passed is False


def test_aggregate_is_mean_not_max() -> None:
    low = _all_metrics(project_f1=0.0)
    high = _all_metrics(project_f1=1.0)
    out = _aggregate([low, high])
    assert out["project_f1"] == 0.5


def test_report_passed_is_false_when_summary_missing() -> None:
    metrics = _all_metrics()
    day = DayEval(
        name="x",
        date="2026-10-31",
        metrics=metrics,
        derived_first={"summaries": 0},
        derived_second={"summaries": 0},
        summary_present=False,
        identical_counts=True,
        narrative_nonempty=False,
        citation_count=0,
        claims=2,
    )
    report = EvalReport(
        mode="replay",
        runtime="replay",
        days=[day],
        aggregate=metrics,
        gate=gate_metrics(metrics),
    )
    assert report.gate["passed"] is True
    assert report.passed is False
    assert report.pipeline_passed is False


def test_narrative_ok_is_not_summary_present() -> None:
    present = DayEval(
        name="x",
        date="2026-10-31",
        metrics={},
        derived_first={"summaries": 1},
        derived_second={"summaries": 1},
        summary_present=True,
        identical_counts=True,
        narrative_nonempty=False,
        citation_count=0,
        claims=2,
    )
    assert present.summary_present is True
    assert present.narrative_ok is False
    uncited = DayEval(
        name="x",
        date="2026-10-31",
        metrics={},
        derived_first={"summaries": 1},
        derived_second={"summaries": 1},
        summary_present=True,
        identical_counts=True,
        narrative_nonempty=True,
        citation_count=0,
        claims=2,
    )
    assert uncited.summary_present is True
    assert uncited.narrative_ok is False
    cited = DayEval(
        name="x",
        date="2026-10-31",
        metrics={},
        derived_first={"summaries": 1},
        derived_second={"summaries": 1},
        summary_present=True,
        identical_counts=True,
        narrative_nonempty=True,
        citation_count=1,
        claims=2,
    )
    assert cited.narrative_ok is True
    no_claims = DayEval(
        name="x",
        date="2026-10-31",
        metrics={},
        derived_first={"summaries": 1},
        derived_second={"summaries": 1},
        summary_present=True,
        identical_counts=True,
        narrative_nonempty=False,
        citation_count=0,
        claims=0,
    )
    assert no_claims.narrative_ok is True


def test_report_passed_is_false_when_counts_drift() -> None:
    metrics = _all_metrics()
    day = DayEval(
        name="x",
        date="2026-10-31",
        metrics=metrics,
        derived_first={"summaries": 1},
        derived_second={"summaries": 2},
        summary_present=True,
        identical_counts=False,
        narrative_nonempty=True,
        citation_count=1,
        claims=1,
    )
    report = EvalReport(
        mode="replay",
        runtime="replay",
        days=[day],
        aggregate=metrics,
        gate=gate_metrics(metrics),
    )
    assert report.passed is False


def test_each_threshold_fails_on_deliberately_bad_value() -> None:
    rules = load_thresholds()
    bad = {
        "project_f1": 0.0,
        "people_f1": 0.0,
        "completed_accuracy": 0.0,
        "decisions_recall": 0.0,
        "decisions_precision": 0.0,
        "commitments_recall": 0.0,
        "commitments_precision": 0.0,
        "unsupported_achievements": 3.0,
        "duplicate_observation_rate": 1.0,
        "noise_non_work_rate": 0.0,
        "claims_with_evidence": 0.0,
        "citations_resolvable": 0.0,
    }
    for name, value in bad.items():
        metrics = _all_metrics(**{name: value})
        gate = gate_metrics(metrics, rules)
        assert gate["passed"] is False, name
        assert name in gate["failures"], name


def test_completed_accuracy_penalizes_false_completions() -> None:
    assert (
        completed_accuracy(
            ["Onboarding Guide section 3 published", "Acme proposal"],
            ["Onboarding Guide section 3 published"],
            ["Acme proposal (still editing at day end)"],
        )
        == 0.5
    )
    assert unsupported_count(["Ghost shipped"], ["Onboarding Guide section 3 published"]) == 1
