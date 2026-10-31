"""day_fragmented_multi: switches, gaps, and an emitted excluded vault window."""

from __future__ import annotations

import json
from pathlib import Path

from watson.config.schema import DEFAULT_EXCLUDED_BUNDLE_IDS
from watson_fixturegen.cadence import expand_schedule
from watson_fixturegen.paths import scenarios_dir
from watson_fixturegen.scenario import load_scenario
from watson_fixturegen.truth import build_truth, skipped_frames, skipped_windows

REPO = Path(__file__).resolve().parents[3]

SCENARIO = scenarios_dir() / "day_fragmented_multi.yaml"
VAULT_BUNDLE = "com.apple.Passwords"


def test_fragmented_day_has_three_projects_and_gaps() -> None:
    scenario = load_scenario(SCENARIO)
    frames = expand_schedule(scenario)
    work = [frame for frame in frames if not frame.window.excluded]
    assert 80 <= len(work) <= 250
    assert len(scenario.truth.projects) >= 3
    stamps = [frame.captured_at for frame in work]
    assert stamps == sorted(stamps)
    # Lunch gap 12:00–13:00 and mid-day idle 14:00–14:25 produce no frames.
    assert not any(stamp.hour == 12 for stamp in stamps)
    assert not any(stamp.hour == 14 and stamp.minute < 25 for stamp in stamps)
    assert any(stamp.hour == 11 for stamp in stamps)
    assert any(stamp.hour == 13 for stamp in stamps)
    assert any(stamp.hour == 14 and stamp.minute >= 25 for stamp in stamps)
    truth = build_truth(scenario, frames)
    assert len(truth["expected_segments"]) >= 15
    assert all(segment["bundle_id"] != VAULT_BUNDLE for segment in truth["expected_segments"])


def test_excluded_vault_window_is_scheduled_and_marked() -> None:
    scenario = load_scenario(SCENARIO)
    frames = expand_schedule(scenario)
    excluded = [frame for frame in frames if frame.window.excluded]
    assert excluded
    assert all(frame.window.bundle_id == VAULT_BUNDLE for frame in excluded)
    assert VAULT_BUNDLE in DEFAULT_EXCLUDED_BUNDLE_IDS
    assert all(frame.window.is_private_browsing for frame in excluded)
    assert all("Passwords" in frame.window.window_title for frame in excluded)
    assert any(
        stamp.hour == 10 and stamp.minute >= 24 for stamp in (f.captured_at for f in excluded)
    )
    skipped = skipped_windows(scenario)
    assert skipped
    assert {row["bundle_id"] for row in skipped} == {VAULT_BUNDLE}
    assert all(row["reason"] == "excluded_app" for row in skipped)
    truth = build_truth(scenario, frames)
    assert truth["skipped"] == skipped_frames(frames)
    assert len(truth["skipped"]) == len(excluded)
    vault = scenario.windows["vault"]
    assert vault.excluded is True
    assert vault.is_private_browsing is True
    assert vault.bundle_id == VAULT_BUNDLE


def test_work_frame_ids_match_committed_non_excluded() -> None:
    """Inserting excluded frames must not rewrite existing work-frame UUIDs."""
    scenario = load_scenario(SCENARIO)
    work_ids = {frame.frame_id for frame in expand_schedule(scenario) if not frame.window.excluded}
    kept: set[str] = set()
    for path in (REPO / "tests" / "fixtures" / "day_fragmented_multi" / "frames").glob("*/*.json"):
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload["bundle_id"] != VAULT_BUNDLE:
            kept.add(path.stem)
    assert work_ids == kept
