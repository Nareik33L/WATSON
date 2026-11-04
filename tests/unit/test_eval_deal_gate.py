"""M3f deal scores in watson eval: report blocks, strict gate, parked rows."""

from __future__ import annotations

import json
from pathlib import Path

from watson.intelligence.eval.deals import (
    NONE,
    V1_DEAL_DAYS,
    V1_PIPELINE_DAYS,
    DayGold,
    DealCorpus,
    Frame,
    GoldSegment,
    PredictedCorpus,
    PredictedDay,
    PredictedSegment,
    baseline_all_none,
    deal_score_report,
    empty_deal_blocks,
    gold_for_eval_run,
    honest_prediction,
    load_deal_corpus,
    predicted_day_from_collect,
    score_deals,
)
from watson.intelligence.eval.run import DayEval, EvalReport, _write_reports
from watson.intelligence.eval.thresholds import (
    DEAL_ACCURACY_MIN,
    deal_quality_passed,
    gate_metrics,
    load_thresholds,
    passing_deal_blocks,
)

REPO = Path(__file__).resolve().parents[2]
FIXTURES = REPO / "tests" / "fixtures"


def _metrics() -> dict[str, float]:
    metrics = {name: 1.0 for name in load_thresholds()}
    metrics["unsupported_achievements"] = 0.0
    metrics["duplicate_observation_rate"] = 0.0
    metrics["decisions_precision"] = 0.84
    metrics["commitments_precision"] = 0.84
    return metrics


def _day(metrics: dict[str, float]) -> DayEval:
    return DayEval(
        name="day_focused",
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


def _report(
    *,
    grouping: dict[str, object],
    summary: dict[str, object],
    privacy_fail: bool = False,
) -> EvalReport:
    metrics = _metrics()
    return EvalReport(
        mode="record",
        runtime="ollama",
        days=[_day(metrics)],
        aggregate=metrics,
        gate=gate_metrics(metrics),
        deal_grouping=dict(grouping),
        deal_summary=dict(summary),
        privacy_fail=privacy_fail,
    )


def test_strict_fails_when_either_deal_accuracy_below_0_85() -> None:
    grouping, summary = passing_deal_blocks()
    low_group = _report(grouping={**grouping, "accuracy": 0.84}, summary=summary)
    low_sum = _report(grouping=grouping, summary={**summary, "accuracy": 0.84})
    assert low_group.pipeline_passed is True
    assert low_sum.pipeline_passed is True
    assert low_group.passed is False
    assert low_sum.passed is False
    assert deal_quality_passed(low_group.deal_grouping, low_group.deal_summary, False) is False


def test_strict_passes_at_0_85_and_honest_gold_grouping() -> None:
    grouping, summary = passing_deal_blocks()
    edge = _report(
        grouping={**grouping, "accuracy": DEAL_ACCURACY_MIN},
        summary={**summary, "accuracy": DEAL_ACCURACY_MIN},
    )
    assert edge.gate["passed"] is True
    assert "decisions_precision" not in (edge.gate.get("failures") or [])
    assert edge.pipeline_passed is True
    assert edge.passed is True

    gold = load_deal_corpus()
    honest = honest_prediction(gold)
    scored = score_deals(gold, honest)
    assert scored.grouping.accuracy >= DEAL_ACCURACY_MIN
    assert scored.summary.accuracy >= DEAL_ACCURACY_MIN
    blocks = deal_score_report(gold, honest)
    hit = _report(grouping=blocks[0], summary=blocks[1], privacy_fail=blocks[2])
    assert hit.passed is True
    assert hit.privacy_fail is False

    none = baseline_all_none(gold)
    miss_blocks = deal_score_report(gold, none)
    miss = _report(grouping=miss_blocks[0], summary=miss_blocks[1], privacy_fail=miss_blocks[2])
    assert miss.deal_grouping["accuracy"] < DEAL_ACCURACY_MIN
    assert miss.pipeline_passed is True
    assert miss.passed is False


def test_parked_quality_rows_do_not_fail_strict() -> None:
    grouping, summary = passing_deal_blocks()
    metrics = _metrics()
    gate = gate_metrics(metrics)
    assert gate["passed"] is True
    assert "decisions_precision" in gate["report_only_failures"]
    assert "commitments_precision" in gate["report_only_failures"]
    report = _report(grouping=grouping, summary=summary)
    assert report.passed is True
    assert report.pipeline_passed is True


def test_privacy_fail_zeros_both_deal_accuracies() -> None:
    bait = Frame("f-bait", "secret privacybait vault", skipped=True, skip_reason="excluded_app")
    gold = DealCorpus(
        days=[
            DayGold(
                name="day_x",
                date="2026-10-31",
                deals=[],
                people=[],
                segments=[
                    GoldSegment(
                        id="s-bait",
                        frame_ids=("f-bait",),
                        deal=NONE,
                        privacy_bait=True,
                    )
                ],
                frames={"f-bait": bait},
                facts=[],
                skipped={"f-bait": bait},
            )
        ]
    )
    leaked = PredictedCorpus(
        days=[
            PredictedDay(
                name="day_x",
                segments=[PredictedSegment("s-bait", ("f-bait",), "deal")],
                claims=[],
            )
        ]
    )
    grouping, summary, privacy_fail = deal_score_report(gold, leaked)
    assert privacy_fail is True
    assert grouping["accuracy"] == 0.0
    assert summary["accuracy"] == 0.0
    report = _report(grouping=grouping, summary=summary, privacy_fail=privacy_fail)
    assert report.pipeline_passed is True
    assert report.passed is False


def test_deal_blocks_omit_titles_ocr_and_prompts() -> None:
    gold = load_deal_corpus()
    grouping, summary, _leak = deal_score_report(gold, honest_prediction(gold))
    blob = repr(grouping) + repr(summary)
    for banned in ("ocr", "prompt", "title", "Window:", "Cluster these"):
        assert banned not in blob
    assert "accuracy" in grouping
    assert "assignment_accuracy" in grouping
    assert "days" in grouping
    assert "accuracy" in summary
    assert "what_happened_recall" in summary
    assert "days" in summary
    assert set(grouping["days"]) == {day.name for day in gold.days}


def test_predicted_day_from_collect_carries_frames_and_evidence() -> None:
    collected = {
        "segment_deals": [
            {
                "segment_id": "seg-1",
                "deal_id": "deal-a",
                "frame_ids": ["f-1", "f-2"],
                "started_at": "2026-10-31T09:00:00Z",
            }
        ],
        "summary_slots": [
            {"deal_id": "deal-a", "slot": "what_happened", "claim_ids": ["c-1"]}
        ],
        "deal_claims": {
            "c-1": {
                "text": "Priya emailed Acme",
                "evidence": [{"frame_id": "f-1", "segment_id": "seg-1"}],
            }
        },
    }
    day = predicted_day_from_collect("day_acme_q4", collected)
    assert day.name == "day_acme_q4"
    assert len(day.segments) == 1
    assert day.segments[0].id == "seg-1"
    assert day.segments[0].frame_ids == ("f-1", "f-2")
    assert day.segments[0].deal == "deal-a"
    assert day.segments[0].started_at == "2026-10-31T09:00:00Z"
    assert len(day.claims) == 1
    assert day.claims[0].id == "c-1"
    assert day.claims[0].text == "Priya emailed Acme"
    assert day.claims[0].deal == "deal-a"
    assert day.claims[0].slot == "what_happened"
    assert len(day.claims[0].evidence) == 1
    assert day.claims[0].evidence[0].frame_id == "f-1"
    assert day.claims[0].evidence[0].segment_id == "seg-1"


def test_gold_for_eval_run_returns_all_days_including_last() -> None:
    replay = gold_for_eval_run(V1_PIPELINE_DAYS, FIXTURES, mode="replay")
    assert [day.name for day in replay.days] == list(V1_PIPELINE_DAYS)
    assert replay.days[-1].name == V1_PIPELINE_DAYS[-1]
    record = gold_for_eval_run(V1_DEAL_DAYS, FIXTURES, mode="record")
    assert [day.name for day in record.days] == list(V1_DEAL_DAYS)
    assert record.days[-1].name == V1_DEAL_DAYS[-1]


def test_structural_failure_fails_strict_with_passing_deal_metrics() -> None:
    grouping, summary = passing_deal_blocks()
    report = _report(grouping=grouping, summary=summary)
    assert deal_quality_passed(report.deal_grouping, report.deal_summary, False) is True
    assert report.pipeline_passed is True
    report.days[0].identical_counts = False
    assert report.pipeline_passed is False
    assert report.passed is False

    narrative = _report(grouping=grouping, summary=summary)
    narrative.days[0].narrative_nonempty = False
    assert narrative.pipeline_passed is False
    assert narrative.passed is False


def test_empty_deal_blocks_fail_closed() -> None:
    grouping, summary = empty_deal_blocks()
    assert grouping["accuracy"] == 0.0
    assert summary["accuracy"] == 0.0
    assert grouping["days"] == {}
    assert summary["days"] == {}
    assert deal_quality_passed(grouping, summary, False) is False
    scored = deal_score_report(DealCorpus(days=[]), PredictedCorpus(days=[]))
    assert scored[0]["accuracy"] == 0.0
    assert scored[1]["accuracy"] == 0.0
    assert scored[2] is False
    report = _report(grouping=scored[0], summary=scored[1], privacy_fail=scored[2])
    assert report.pipeline_passed is True
    assert report.passed is False


def test_deal_quality_passed_privacy_fail_true_and_report_json(tmp_path: Path) -> None:
    grouping, summary = passing_deal_blocks()
    assert deal_quality_passed(grouping, summary, True) is False
    assert deal_quality_passed(grouping, summary, False) is True
    report = _report(grouping=grouping, summary=summary, privacy_fail=True)
    assert report.privacy_fail is True
    assert report.passed is False
    _write_reports(tmp_path, report)
    payload = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    assert payload["privacy_fail"] is True
    markdown = (tmp_path / "report.md").read_text(encoding="utf-8")
    assert "privacy_fail: **True**" in markdown


def test_deal_quality_passed_missing_or_malformed_blocks() -> None:
    grouping, summary = passing_deal_blocks()
    assert deal_quality_passed(None, summary, False) is False
    assert deal_quality_passed(grouping, None, False) is False
    assert deal_quality_passed({}, {}, False) is False
    assert deal_quality_passed({"accuracy": "nope"}, summary, False) is False
    assert deal_quality_passed(grouping, {"wrong": 1.0}, False) is False
    assert deal_quality_passed({"accuracy": None}, {"accuracy": 1.0}, False) is False
    assert deal_quality_passed(grouping, summary, False) is True
