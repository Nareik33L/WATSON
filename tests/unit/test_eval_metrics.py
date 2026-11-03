"""§11.2 metric mutants: precision/recall swap, off-by-one thresholds, empty F1."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from watson.intelligence.eval.metrics import (
    ITEM_MATCH_JACCARD_MIN,
    completed_accuracy,
    duplicate_rate,
    f1_score,
    item_jaccard,
    items_match,
    names_match,
    pair_item_matches,
    pair_matches,
    precision,
    recall,
    token_jaccard,
    unsupported_count,
)
from watson.intelligence.eval.run import DayEval, EvalReport, _aggregate
from watson.intelligence.eval.thresholds import (
    Threshold,
    gate_metrics,
    load_duplicate_jaccard_min,
    load_thresholds,
)


def test_empty_sets_score_perfect() -> None:
    assert f1_score([], []) == 1.0
    assert precision([], []) == 1.0
    assert recall([], []) == 1.0
    assert completed_accuracy([], [], []) == 1.0
    assert unsupported_count([], []) == 0


def test_empty_predictions_with_truth_are_not_vacuous_precision() -> None:
    """Empty predictions vs non-empty truth is 0.0, matching recall's empty-pred case.

    Both-empty stays 1.0 (same as recall on empty truth) and the day stays in
    the average — we do not drop empty days from `_aggregate`.
    """
    gold = ["Send revised pricing to Priya by Friday"]
    assert precision([], gold) == 0.0
    assert recall([], gold) == 0.0
    assert precision([], []) == 1.0
    assert recall([], []) == 1.0


def test_five_day_aggregate_fails_when_one_day_predicts_nothing() -> None:
    """A silent day with gold items must not inflate day-average precision past the gate.

    3 days at P=0.8/R=1.0, 1 day with no predictions but gold, 1 both-empty day.
    Vacuous P=1.0 on the silent day averaged to 0.88 and passed the 0.85 gate
    even though real precision is 0.80. After the fix the silent day is 0.0
    (both-empty stays 1.0, like recall) so the mean is 0.68 and the gate fails.
    """
    gold = [f"commitment {index}" for index in range(4)]
    predicted = [*gold, "ghost extra"]
    assert precision(predicted, gold) == 0.8
    assert recall(predicted, gold) == 1.0
    silent = precision([], gold)
    empty = precision([], [])
    assert silent == 0.0
    assert empty == 1.0
    days = [
        _all_metrics(commitments_precision=precision(predicted, gold), commitments_recall=1.0),
        _all_metrics(commitments_precision=precision(predicted, gold), commitments_recall=1.0),
        _all_metrics(commitments_precision=precision(predicted, gold), commitments_recall=1.0),
        _all_metrics(
            commitments_precision=silent,
            commitments_recall=recall([], gold),
        ),
        _all_metrics(commitments_precision=empty, commitments_recall=recall([], [])),
    ]
    aggregate = _aggregate(days)
    assert aggregate["commitments_precision"] == pytest.approx(0.68)
    assert aggregate["commitments_recall"] == pytest.approx(0.80)
    vacuous_mean = (0.8 + 0.8 + 0.8 + 1.0 + 1.0) / 5
    assert vacuous_mean == pytest.approx(0.88)
    assert aggregate["commitments_precision"] != pytest.approx(vacuous_mean)
    gate = gate_metrics(aggregate, load_thresholds())
    assert gate["passed"] is False
    assert "commitments_precision" in gate["failures"]


def test_pair_matches_dedup_one_gold_per_prediction() -> None:
    """Two copies of the same prediction consume one gold (1:1), not two TPs."""
    predicted = [
        "Send revised pricing to Priya by Friday",
        "Send revised pricing to Priya by Friday",
    ]
    truth = ["Send revised pricing to Priya by Friday"]
    assert pair_matches(predicted, truth) == 1
    assert precision(predicted, truth) == 0.5
    assert recall(predicted, truth) == 1.0


def test_matching_tolerance_normalizes_case_and_punctuation() -> None:
    """Surviving mutant: drop normalize() and only exact raw strings match."""
    gold = "Send revised pricing to Priya by Friday"
    assert names_match("SEND REVISED PRICING TO PRIYA BY FRIDAY!", gold)
    assert names_match("Send revised pricing, to Priya, by Friday.", gold)
    assert precision(["SEND REVISED PRICING TO PRIYA BY FRIDAY!"], [gold]) == 1.0


def test_matching_tolerance_rejects_different_person_day_object() -> None:
    """Surviving mutant: any token overlap or always-true match."""
    gold = "Send the Harbor checklist to Ellis Voss by Wednesday"
    assert names_match(gold, gold)
    assert not names_match("Send the Harbor checklist to Mina Okonkwo by Wednesday", gold)
    assert not names_match("Send the Harbor checklist to Ellis Voss by Friday", gold)
    assert not names_match("Send the Harbor hours grid to Ellis Voss by Wednesday", gold)
    assert (
        precision(
            [
                "Send the Harbor checklist to Mina Okonkwo by Wednesday",
                "Send the Harbor checklist to Ellis Voss by Friday",
                "Send the Harbor hours grid to Ellis Voss by Wednesday",
            ],
            [gold],
        )
        == 0.0
    )


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


def _obs(
    started_at: str,
    *,
    bundle: str = "com.lumen.quill",
    window: str = "acme q4 proposal — draft",
    description: str = "Editing the Acme Q4 proposal draft.",
    segment_id: str = "",
) -> dict[str, str]:
    return {
        "started_at": started_at,
        "bundle_id": bundle,
        "window_title_norm": window,
        "description": description,
        "segment_id": segment_id or started_at,
    }


def test_duplicate_rate_oversegmentation_is_not_zero() -> None:
    """Adjacent same-window same-description observations are extras."""
    rows = [
        _obs("2026-10-31T09:00:00Z", segment_id="a"),
        _obs("2026-10-31T09:08:00Z", segment_id="b"),
        _obs("2026-10-31T09:16:00Z", segment_id="c"),
    ]
    assert duplicate_rate(rows) == 2 / 3
    assert duplicate_rate(rows) > 0.0


def test_duplicate_rate_revisit_after_other_window_is_zero() -> None:
    rows = [
        _obs("2026-10-31T09:00:00Z", segment_id="draft-1"),
        _obs(
            "2026-10-31T10:40:00Z",
            bundle="com.lumen.compass",
            window="cedar daily — harbor ferry",
            description="Reading civic news in Cedar Daily.",
            segment_id="news",
        ),
        _obs("2026-10-31T10:46:00Z", segment_id="draft-2"),
    ]
    assert duplicate_rate(rows) == 0.0


def test_duplicate_rate_empty_is_zero() -> None:
    assert duplicate_rate([]) == 0.0


def test_duplicate_rate_ignores_different_app() -> None:
    rows = [
        _obs("2026-10-31T09:00:00Z", segment_id="a"),
        _obs("2026-10-31T09:08:00Z", bundle="com.lumen.mail", segment_id="b"),
    ]
    assert duplicate_rate(rows) == 0.0


def test_duplicate_rate_ignores_different_window() -> None:
    rows = [
        _obs("2026-10-31T09:00:00Z", segment_id="a"),
        _obs(
            "2026-10-31T09:08:00Z",
            window="acme q4 proposal — notes",
            segment_id="b",
        ),
    ]
    assert duplicate_rate(rows) == 0.0


def test_duplicate_rate_ignores_unrelated_text() -> None:
    left = "Editing the Acme Q4 proposal draft."
    right = "Checking the Lumen Desk inbox."
    assert token_jaccard(left, right) < 0.5
    rows = [
        _obs("2026-10-31T09:00:00Z", description=left, segment_id="a"),
        _obs("2026-10-31T09:08:00Z", description=right, segment_id="b"),
    ]
    assert duplicate_rate(rows) == 0.0


def test_duplicate_rate_paraphrase_with_rotated_verb_counts() -> None:
    left = "Editing the Acme Q4 proposal draft."
    right = "Drafting the Acme Q4 proposal."
    assert token_jaccard(left, right) >= 0.5
    assert left.casefold().split()[0] != right.casefold().split()[0]
    rows = [
        _obs("2026-10-31T09:00:00Z", description=left, segment_id="a"),
        _obs("2026-10-31T09:08:00Z", description=right, segment_id="b"),
    ]
    assert duplicate_rate(rows) == 0.5


def _write_jaccard_config(tmp_path: Path, jaccard_min: float) -> Path:
    path = tmp_path / "thresholds.json"
    path.write_text(
        json.dumps(
            {
                "duplicate_observation_rate": {
                    "max_exclusive": 0.05,
                    "jaccard_min": jaccard_min,
                }
            }
        ),
        encoding="utf-8",
    )
    return path


def test_duplicate_rate_uses_configured_jaccard_min(tmp_path: Path) -> None:
    """A hardcoded 0.5 cutoff would still count a 0.6 pair under jaccard_min=0.8."""
    left = "alpha beta gamma"
    right = "alpha beta gamma delta epsilon"
    assert token_jaccard(left, right) == 0.6
    rows = [
        _obs("2026-10-31T09:00:00Z", description=left, segment_id="a"),
        _obs("2026-10-31T09:08:00Z", description=right, segment_id="b"),
    ]
    assert load_duplicate_jaccard_min() == 0.5
    assert duplicate_rate(rows) == 0.5
    strict = _write_jaccard_config(tmp_path, 0.8)
    assert load_duplicate_jaccard_min(strict) == 0.8
    assert duplicate_rate(rows, thresholds_path=strict) == 0.0


def test_jaccard_min_zero_is_rejected(tmp_path: Path) -> None:
    path = _write_jaccard_config(tmp_path, 0)
    with pytest.raises(ValueError, match=r"\(0, 1\]"):
        load_duplicate_jaccard_min(path)


def test_jaccard_min_negative_is_rejected(tmp_path: Path) -> None:
    path = _write_jaccard_config(tmp_path, -0.1)
    with pytest.raises(ValueError, match=r"\(0, 1\]"):
        load_duplicate_jaccard_min(path)


def test_jaccard_min_above_one_is_rejected(tmp_path: Path) -> None:
    path = _write_jaccard_config(tmp_path, 1.01)
    with pytest.raises(ValueError, match=r"\(0, 1\]"):
        load_duplicate_jaccard_min(path)


def test_jaccard_min_one_is_accepted(tmp_path: Path) -> None:
    """Upper bound is inclusive: a `< 1` check would reject 1.0."""
    path = _write_jaccard_config(tmp_path, 1.0)
    assert load_duplicate_jaccard_min(path) == 1.0
    left = "alpha beta gamma"
    right = "alpha beta gamma"
    assert token_jaccard(left, right) == 1.0
    rows = [
        _obs("2026-10-31T09:00:00Z", description=left, segment_id="a"),
        _obs("2026-10-31T09:08:00Z", description=right, segment_id="b"),
    ]
    assert duplicate_rate(rows, thresholds_path=path) == 0.5
    below = [
        _obs("2026-10-31T09:00:00Z", description="alpha beta gamma", segment_id="a"),
        _obs("2026-10-31T09:08:00Z", description="alpha beta gamma delta", segment_id="b"),
    ]
    assert token_jaccard("alpha beta gamma", "alpha beta gamma delta") < 1.0
    assert duplicate_rate(below, thresholds_path=path) == 0.0


def test_jaccard_min_json_true_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "thresholds.json"
    path.write_text(
        '{"duplicate_observation_rate": {"max_exclusive": 0.05, "jaccard_min": true}}',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match=r"\(0, 1\]"):
        load_duplicate_jaccard_min(path)


def test_duplicate_jaccard_boundary_is_inclusive() -> None:
    assert load_duplicate_jaccard_min() == 0.5
    at = ("alpha beta", "alpha beta gamma delta")
    below = ("one two three four five", "one two three six seven")
    assert token_jaccard(*at) == 0.5
    assert token_jaccard(*below) < 0.5
    assert (
        duplicate_rate(
            [
                _obs("2026-10-31T09:00:00Z", description=at[0], segment_id="a"),
                _obs("2026-10-31T09:08:00Z", description=at[1], segment_id="b"),
            ]
        )
        == 0.5
    )
    assert (
        duplicate_rate(
            [
                _obs("2026-10-31T09:00:00Z", description=below[0], segment_id="a"),
                _obs("2026-10-31T09:08:00Z", description=below[1], segment_id="b"),
            ]
        )
        == 0.0
    )


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


def test_pipeline_passed_is_false_when_a_day_has_error() -> None:
    """R5: pipeline_passed ignores a day's error when the rest of the gate is green."""
    metrics = _all_metrics()
    ok = DayEval(
        name="ok",
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
    broken = DayEval(
        name="broken",
        date="2026-10-31",
        metrics=metrics,
        derived_first={"summaries": 1},
        derived_second={"summaries": 1},
        summary_present=True,
        identical_counts=True,
        narrative_nonempty=True,
        citation_count=1,
        claims=1,
        error="day exploded",
    )
    report = EvalReport(
        mode="replay",
        runtime="replay",
        days=[ok, broken],
        aggregate=metrics,
        gate=gate_metrics(metrics),
        failure_reason=None,
    )
    assert report.gate["passed"] is True
    assert report.failure_reason is None
    assert all(day.identical_counts and day.narrative_ok for day in report.days)
    assert report.pipeline_passed is False
    assert report.passed is False


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


def test_pipeline_passed_false_when_claims_have_empty_narrative() -> None:
    """narrative_nonempty is not vacuously true: claims + empty body fail the pipeline."""
    metrics = _all_metrics()
    day = DayEval(
        name="x",
        date="2026-10-31",
        metrics=metrics,
        derived_first={"summaries": 1},
        derived_second={"summaries": 1},
        summary_present=True,
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
    assert day.narrative_ok is False
    assert report.pipeline_passed is False
    assert report.passed is False


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


def test_item_match_threshold_is_the_named_constant() -> None:
    """A single named cutoff; 0.7 rejects a one-token swap on a 5-token item."""
    assert ITEM_MATCH_JACCARD_MIN == 0.7
    five = ("alpha beta gamma delta epsilon", "alpha beta gamma delta zeta")
    assert item_jaccard(*five) == pytest.approx(4 / 6)
    assert item_jaccard(*five) < ITEM_MATCH_JACCARD_MIN
    assert not items_match(*five)


def test_item_match_accepts_bakeoff_commitment_paraphrase() -> None:
    """FP-table true match: first-person + stopwords vs gold addressee."""
    predicted = "I will send the slide outline tomorrow"
    gold = "Send Priya the slide outline tomorrow"
    assert item_jaccard(predicted, gold) == pytest.approx(4 / 5)
    assert items_match(predicted, gold)
    assert pair_item_matches([predicted], [gold]) == 1
    assert precision([predicted], [gold], matches=pair_item_matches) == 1.0
    assert recall([predicted], [gold], matches=pair_item_matches) == 1.0
    assert not names_match(predicted, gold)


def test_item_match_accepts_bakeoff_completed_paraphrase() -> None:
    """FP-table true match: copula 'is' is a stopword, not a different object."""
    predicted = "Cedar Onboarding page 1 is published"
    gold = "Cedar Onboarding page 1 published"
    assert item_jaccard(predicted, gold) == 1.0
    assert items_match(predicted, gold)
    not_done = ["Cedar Onboarding page 2 (still a draft)"]
    assert completed_accuracy([predicted], [gold], not_done) == 1.0
    assert unsupported_count([predicted], [gold]) == 0
    assert not names_match(predicted, gold)


def test_item_match_rejects_different_person_day_object() -> None:
    """Near-miss negatives: a different person, day, or object must not match."""
    gold = "Send Priya the slide outline tomorrow"
    person = "I will send Mina the slide outline tomorrow"
    day = "I will send the slide outline Friday"
    obj = "I will send the hours grid tomorrow"
    assert item_jaccard(person, gold) == pytest.approx(4 / 6)
    assert item_jaccard(day, gold) == pytest.approx(3 / 6)
    assert item_jaccard(obj, gold) == pytest.approx(2 / 7)
    assert not items_match(person, gold)
    assert not items_match(day, gold)
    assert not items_match(obj, gold)
    assert pair_item_matches([person, day, obj], [gold]) == 0
    assert precision([person, day, obj], [gold], matches=pair_item_matches) == 0.0


def test_item_match_rejects_different_completed_object() -> None:
    """Page 2 is not page 1 — one content token is enough to fall under 0.7."""
    gold = "Cedar Onboarding page 1 published"
    other = "Cedar Onboarding page 2 is published"
    assert item_jaccard(other, gold) == pytest.approx(4 / 6)
    assert not items_match(other, gold)
    assert unsupported_count([other], [gold]) == 1


def test_pair_item_matches_is_one_to_one_by_best_score() -> None:
    """Each prediction consumes at most one gold; higher Jaccard wins first."""
    gold = "Send Priya the slide outline tomorrow"
    weaker = "I will send Priya the slide outline"
    exact = "Send Priya the slide outline tomorrow"
    assert item_jaccard(weaker, gold) == pytest.approx(4 / 5)
    assert item_jaccard(exact, gold) == 1.0
    assert pair_item_matches([weaker, exact], [gold]) == 1
    assert precision([weaker, exact], [gold], matches=pair_item_matches) == 0.5
    assert recall([weaker, exact], [gold], matches=pair_item_matches) == 1.0
    two_gold = [gold, "Keep the Harbor walkthrough on Thursday"]
    assert pair_item_matches([weaker, exact], two_gold) == 1


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
