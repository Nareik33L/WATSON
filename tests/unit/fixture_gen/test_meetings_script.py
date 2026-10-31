from __future__ import annotations

from pathlib import Path

import pytest

from watson_fixturegen.meetings.script import load_meeting_script
from watson_fixturegen.meetings.transcript import dump_meeting_truth, spoken_from_transcript
from watson_fixturegen.paths import meetings_scripts_dir
from watson_fixturegen.secrets import scan_texts

SCRIPTS = meetings_scripts_dir()


def test_a0_scripts_exist() -> None:
    names = {path.name for path in SCRIPTS.glob("*.yaml")}
    assert names >= {
        "standup_12m.yaml",
        "speech_short.yaml",
        "silence.yaml",
        "tone.yaml",
        "one_second.yaml",
        "corrupt.yaml",
        "unsupported.yaml",
    }
    assert "customer_45m.yaml" not in names
    assert "workshop_90m.yaml" not in names


def test_standup_script_has_truth_traps_and_fictional_names() -> None:
    script = load_meeting_script(SCRIPTS / "standup_12m.yaml")
    assert script.kind == "dialogue"
    assert script.target_duration_s == 720
    assert set(script.speakers) == {"Nora Voss", "Samir Qureshi", "Leah Okonkwo", "Benji Calder"}
    assert script.truth.participants == (
        "Nora Voss",
        "Samir Qureshi",
        "Leah Okonkwo",
        "Benji Calder",
    )
    assert script.truth.decisions
    owners = {item["owner"] for item in script.truth.commitments}
    assert owners == {"Samir Qureshi", "Leah Okonkwo", "Benji Calder", "Nora Voss"}
    assert all("deadline" in item for item in script.truth.commitments)
    assert script.truth.project_updates
    assert script.truth.discussion_points
    kinds = {item["kind"] for item in script.truth.traps}
    assert kinds == {
        "suggestion-not-decision",
        "retracted-deadline",
        "absent-person-mentioned",
    }
    absent = next(item for item in script.truth.traps if item["kind"] == "absent-person-mentioned")
    assert absent["name"] == "Helen Cho"
    assert "Helen Cho" not in script.truth.participants


def test_scripts_have_no_real_brands_or_secrets() -> None:
    texts = [path.read_text(encoding="utf-8") for path in sorted(SCRIPTS.glob("*.yaml"))]
    assert scan_texts(texts) == []


def test_load_rejects_unknown_kind(tmp_path: Path) -> None:
    path = tmp_path / "bad.yaml"
    path.write_text("kind: karaoke\nmeta:\n  name: no\n", encoding="utf-8")
    with pytest.raises(ValueError, match="kind"):
        load_meeting_script(path)


def test_dialogue_requires_lines(tmp_path: Path) -> None:
    path = tmp_path / "empty.yaml"
    path.write_text(
        "kind: dialogue\nmeta:\n  name: empty\nspeakers:\n  A: Samantha\nlines: []\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="lines"):
        load_meeting_script(path)


def test_truth_dump_is_stable() -> None:
    script = load_meeting_script(SCRIPTS / "standup_12m.yaml")
    first = dump_meeting_truth(script, duration_ms=720000)
    second = dump_meeting_truth(script, duration_ms=720000)
    assert first == second
    assert "Nora Voss" in first
    assert "retracted-deadline" in first


def test_spoken_from_transcript_strips_labels() -> None:
    text = "[00:00.000-00:03.200] Nora Voss: Good morning everyone.\n"
    assert spoken_from_transcript(text) == "Good morning everyone."
