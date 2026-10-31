"""day_fragmented_multi: switches, gaps, and an excluded private window."""

from __future__ import annotations

from watson_fixturegen.cadence import expand_schedule
from watson_fixturegen.paths import scenarios_dir
from watson_fixturegen.scenario import load_scenario
from watson_fixturegen.truth import build_truth, skipped_windows

SCENARIO = scenarios_dir() / "day_fragmented_multi.yaml"
VAULT_BUNDLE = "com.cedar.vault"


def test_fragmented_day_has_three_projects_and_gaps() -> None:
    scenario = load_scenario(SCENARIO)
    frames = expand_schedule(scenario)
    assert 80 <= len(frames) <= 250
    assert len(scenario.truth.projects) >= 3
    stamps = [frame.captured_at for frame in frames]
    assert stamps == sorted(stamps)
    # Lunch gap 12:00–13:00 and mid-day idle 14:00–14:25 produce no frames.
    assert not any(stamp.hour == 12 for stamp in stamps)
    assert not any(stamp.hour == 14 and stamp.minute < 25 for stamp in stamps)
    assert any(stamp.hour == 11 for stamp in stamps)
    assert any(stamp.hour == 13 for stamp in stamps)
    assert any(stamp.hour == 14 and stamp.minute >= 25 for stamp in stamps)
    truth = build_truth(scenario, frames)
    assert len(truth["expected_segments"]) >= 15


def test_excluded_vault_window_is_not_scheduled() -> None:
    scenario = load_scenario(SCENARIO)
    frames = expand_schedule(scenario)
    assert all(frame.window.bundle_id != VAULT_BUNDLE for frame in frames)
    assert all(not frame.window.excluded for frame in frames)
    assert all("Private Browsing" not in frame.window.window_title for frame in frames)
    skipped = skipped_windows(scenario)
    assert skipped
    assert {row["bundle_id"] for row in skipped} == {VAULT_BUNDLE}
    assert all(row["reason"] == "excluded_app" for row in skipped)
    truth = build_truth(scenario, frames)
    assert truth["skipped"] == skipped
    vault = scenario.windows["vault"]
    assert vault.excluded is True
    assert vault.is_private_browsing is True
