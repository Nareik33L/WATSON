"""Replay report includes deal blocks; record names cover every deal_gold day."""

from __future__ import annotations

import json
from pathlib import Path

from tests.replay_support import TEST_KEY, V1_DAY_FIXTURES
from watson.intelligence.eval.deals import (
    V1_DEAL_DAYS,
    V1_PIPELINE_DAYS,
    deal_gold_root,
    gold_for_eval_run,
    load_deal_day,
    load_v1_deal_day,
)
from watson.intelligence.eval.run import (
    _resolve_fixture,
    eval_fixture_names,
    run_eval,
)

REPO = Path(__file__).resolve().parents[2]
FIXTURES = REPO / "tests" / "fixtures"
RECORDINGS = FIXTURES / "eval" / "recordings.json"


def _frame_ids(day: object) -> set[str]:
    return {frame_id for segment in day.segments for frame_id in segment.frame_ids}


def test_eval_fixture_names_record_includes_deal_gold() -> None:
    replay = eval_fixture_names("replay")
    record = eval_fixture_names("record")
    only = eval_fixture_names("record", ("day_focused",))
    pipeline = tuple(name for name in V1_DAY_FIXTURES if name not in V1_DEAL_DAYS)
    assert replay == V1_DAY_FIXTURES
    assert record[: len(pipeline)] == pipeline
    assert record[len(pipeline) :] == V1_DEAL_DAYS
    assert only == ("day_focused",)
    assert set(V1_DEAL_DAYS) <= set(record)
    assert record.count("day_near_empty") == 1


def test_resolve_fixture_materializes_every_deal_gold_day(tmp_path: Path) -> None:
    out = tmp_path / "eval-out"
    for name in V1_DEAL_DAYS:
        dest = _resolve_fixture(
            name, fixtures_root=FIXTURES, out_dir=out, prefer_deal_gold=True
        )
        assert dest == out / "deal_gold" / name
        assert (dest / "manifest.json").is_file()
        gold = load_deal_day(deal_gold_root() / name)
        assert gold.name == name
        payload = json.loads((dest / "manifest.json").read_text(encoding="utf-8"))
        assert payload["name"] == name
        assert payload["frames"]


def test_replay_report_contains_deal_grouping_and_summary_blocks(
    tmp_path: Path, key_provider: object
) -> None:
    out = tmp_path / "eval"
    report = run_eval(
        fixtures_root=FIXTURES,
        names=V1_DAY_FIXTURES,
        mode="replay",
        runtime_name="replay",
        recordings=RECORDINGS,
        out_dir=out,
        key=TEST_KEY,
    )
    payload = json.loads((out / "report.json").read_text(encoding="utf-8"))
    assert "deal_grouping" in payload
    assert "deal_summary" in payload
    grouping = payload["deal_grouping"]
    summary = payload["deal_summary"]
    for block in (grouping, summary):
        assert "accuracy" in block
        assert "days" in block
        assert isinstance(block["days"], dict)
    assert "assignment_accuracy" in grouping
    assert "deal_segment_recall" in grouping
    assert "pairwise_f1" in grouping
    assert "claim_precision" in summary
    assert "what_happened_recall" in summary
    assert "next_step_recall" in summary
    assert "who_involved_recall" in summary
    blob = json.dumps(grouping) + json.dumps(summary)
    assert "Cluster these" not in blob
    assert "Write the daily" not in blob
    assert report.pipeline_passed
    assert payload["pipeline_passed"] is True
    # Hand-authored recordings report deal metrics; they do not fail replay CI.
    assert report.pipeline_passed is True
    assert payload["privacy_fail"] is False
    assert set(grouping["days"]) == set(V1_PIPELINE_DAYS)
    assert "day_focused" not in grouping["days"]
    assert grouping["segments"] > 0
    assert grouping["deal_segments"] > 0
    slot_gold = summary.get("slot_gold") or {}
    assert sum(int(value) for value in slot_gold.values()) > 0
    for day in report.days:
        assert day.predicted is not None
        assert day.predicted.name == day.name
        assert day.predicted.segments


def test_gold_for_eval_run_pins_mode_sources() -> None:
    """Replay scores overlay days; record scores materialized deal_gold days."""
    overlay_empty = load_v1_deal_day(FIXTURES / "day_near_empty")
    gold_empty = load_deal_day(deal_gold_root() / "day_near_empty")
    overlay_frames = _frame_ids(overlay_empty)
    gold_frames = _frame_ids(gold_empty)
    assert overlay_frames
    assert gold_frames
    assert overlay_frames != gold_frames

    replay_names = eval_fixture_names("replay")
    replay = gold_for_eval_run(replay_names, FIXTURES, mode="replay")
    assert replay_names == V1_PIPELINE_DAYS
    assert [day.name for day in replay.days] == list(V1_PIPELINE_DAYS)
    assert replay.days[-1].name == "day_near_empty"
    replay_empty = next(day for day in replay.days if day.name == "day_near_empty")
    assert _frame_ids(replay_empty) == overlay_frames
    assert _frame_ids(replay_empty) != gold_frames

    record_names = eval_fixture_names("record")
    record = gold_for_eval_run(record_names, FIXTURES, mode="record")
    assert [day.name for day in record.days] == list(V1_DEAL_DAYS)
    assert record.days[-1].name == V1_DEAL_DAYS[-1] == "day_near_empty"
    record_empty = next(day for day in record.days if day.name == "day_near_empty")
    assert _frame_ids(record_empty) == gold_frames
    assert _frame_ids(record_empty) != overlay_frames

    replay_as_record = gold_for_eval_run(replay_names, FIXTURES, mode="record")
    assert [day.name for day in replay_as_record.days] == ["day_near_empty"]
    assert _frame_ids(replay_as_record.days[0]) == gold_frames


def test_pipeline_output_reaches_scorer_as_predicted(
    tmp_path: Path, key_provider: object
) -> None:
    """_eval_one stores collect_predicted on DayEval.predicted for the scorer."""
    out = tmp_path / "eval-predicted"
    report = run_eval(
        fixtures_root=FIXTURES,
        names=("day_near_empty",),
        mode="replay",
        runtime_name="replay",
        recordings=RECORDINGS,
        out_dir=out,
        key=TEST_KEY,
    )
    assert len(report.days) == 1
    day = report.days[0]
    assert day.predicted is not None
    assert day.predicted.name == "day_near_empty"
    assert day.predicted.segments
    assert any(segment.frame_ids for segment in day.predicted.segments)
