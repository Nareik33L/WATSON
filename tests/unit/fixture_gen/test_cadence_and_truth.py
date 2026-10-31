from __future__ import annotations

import uuid
from pathlib import Path

from watson_fixturegen.cadence import (
    expand_schedule,
    fixture_namespace,
    frame_id_for,
    normalize_title,
)
from watson_fixturegen.paths import scenarios_dir
from watson_fixturegen.scenario import load_scenario
from watson_fixturegen.truth import build_truth, dump_truth, expected_segments, pick_sample

SCENARIO = scenarios_dir() / "day_acme_q4.yaml"


def test_focused_day_schedule_is_in_range() -> None:
    scenario = load_scenario(SCENARIO)
    frames = expand_schedule(scenario)
    assert 120 <= len(frames) <= 200
    ids = [frame.frame_id for frame in frames]
    assert len(set(ids)) == len(ids)
    assert [frame.index for frame in frames] == list(range(1, len(frames) + 1))
    stamps = [frame.captured_at for frame in frames]
    assert stamps == sorted(stamps)
    assert stamps[0].isoformat(timespec="seconds").startswith(scenario.date)
    first = uuid.UUID(frames[0].frame_id)
    assert first.version == 5
    assert frames[0].frame_id == frame_id_for(scenario.name, scenario.seed, 1)
    assert first == uuid.uuid5(fixture_namespace(scenario.name, scenario.seed), "1")


def test_truth_and_sample_are_deterministic() -> None:
    scenario = load_scenario(SCENARIO)
    frames = expand_schedule(scenario)
    first = dump_truth(build_truth(scenario, frames))
    second = dump_truth(build_truth(scenario, frames))
    assert first == second
    sample = pick_sample([frame.frame_id for frame in frames], 50)
    assert sample == pick_sample([frame.frame_id for frame in frames], 50)
    assert len(sample) == 50
    assert sample[0] == frames[0].frame_id
    assert sample[-1] == frames[-1].frame_id


def test_expected_segments_follow_d3() -> None:
    frames = expand_schedule(load_scenario(SCENARIO))
    segments = expected_segments(frames)
    assert len(segments) >= 8
    covered: list[str] = []
    for segment in segments:
        covered.extend(segment["frame_ids"])
        assert segment["start_frame"] == segment["frame_ids"][0]
        assert segment["end_frame"] == segment["frame_ids"][-1]
        assert segment["fixture_index"][0] <= segment["fixture_index"][1]
        titles = {
            normalize_title(frame.window.window_title)
            for frame in frames
            if frame.frame_id in segment["frame_ids"]
        }
        assert len(titles) == 1
    assert covered == [frame.frame_id for frame in frames]


def test_decisions_and_commitments_meet_bar() -> None:
    scenario = load_scenario(SCENARIO)
    truth = build_truth(scenario, expand_schedule(scenario))
    assert len(truth["decisions"]) >= 2
    assert len(truth["commitments"]) >= 2
    assert truth["completed"]
    for decision in truth["decisions"]:
        assert decision["evidence_frames"]
    assert {row["status"] for row in truth["commitments"]} == {"open"}


def test_cli_dry_run(tmp_path: Path) -> None:
    from watson_fixturegen.cli import main

    code = main(["render", str(SCENARIO), "--out", str(tmp_path / "out"), "--dry-run"])
    assert code == 0
    assert (tmp_path / "out" / "truth.yaml").is_file()
    assert (tmp_path / "out" / "sample50.txt").is_file()
    assert not (tmp_path / "out" / "frames").exists()
