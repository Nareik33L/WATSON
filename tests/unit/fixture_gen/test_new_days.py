"""Schedule and truth checks for the three M2b V1 day scenarios."""

from __future__ import annotations

from watson.config.schema import DEFAULT_EXCLUDED_BUNDLE_IDS
from watson_fixturegen.cadence import expand_schedule
from watson_fixturegen.paths import scenarios_dir
from watson_fixturegen.scenario import load_scenario
from watson_fixturegen.truth import build_truth

VAULT_BUNDLE = "com.apple.Passwords"


def test_meeting_heavy_is_mostly_room_calls() -> None:
    scenario = load_scenario(scenarios_dir() / "day_meeting_heavy.yaml")
    frames = expand_schedule(scenario)
    assert 60 <= len(frames) <= 140
    room = [frame for frame in frames if frame.window.bundle_id == "com.lumen.room"]
    assert len(room) >= 30
    truth = build_truth(scenario, frames)
    assert len(truth["expected_segments"]) >= 8
    assert "skipped" not in truth


def test_near_empty_is_short() -> None:
    scenario = load_scenario(scenarios_dir() / "day_near_empty.yaml")
    frames = expand_schedule(scenario)
    assert 6 <= len(frames) <= 20
    truth = build_truth(scenario, frames)
    assert len(truth["expected_segments"]) == 2
    assert not any(stamp.hour in {10, 11, 12, 13} for stamp in (f.captured_at for f in frames))


def test_sensitive_traps_mark_banking_and_vault() -> None:
    scenario = load_scenario(scenarios_dir() / "day_sensitive_traps.yaml")
    frames = expand_schedule(scenario)
    excluded = [frame for frame in frames if frame.window.excluded]
    work = [frame for frame in frames if not frame.window.excluded]
    assert excluded
    assert work
    bundles = {frame.window.bundle_id for frame in excluded}
    assert VAULT_BUNDLE in bundles
    assert VAULT_BUNDLE in DEFAULT_EXCLUDED_BUNDLE_IDS
    assert any("Sign in" in frame.window.window_title for frame in excluded)
    truth = build_truth(scenario, frames)
    assert {row["bundle_id"] for row in truth["skipped"]} == bundles
    assert all(segment["bundle_id"] != VAULT_BUNDLE for segment in truth["expected_segments"])
    planted = "\n".join(frame.corpus for frame in work)
    assert "4111111111111111" in planted
    assert "482913" in planted
    assert "cedar-trap-pass-9f3" in planted
