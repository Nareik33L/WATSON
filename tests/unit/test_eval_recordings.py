"""RecordingRuntime: replay committed JSON only, no truth lexicon."""

from __future__ import annotations

from pathlib import Path

from watson.intelligence.eval.recordings import RecordingRuntime
from watson.intelligence.runtime.types import RuntimeUnavailable

REPO = Path(__file__).resolve().parents[2]
RECORDINGS = REPO / "tests" / "fixtures" / "eval" / "recordings.json"


def test_replay_uses_recording_without_augmentation() -> None:
    runtime = RecordingRuntime(path=RECORDINGS, mode="replay")
    blob = (
        "Window: Mystery App\n"
        "1. Internal Onboarding Guide and Priya Natarajan appear in chrome\n"
        "2. Section 3 of the Internal Onboarding Guide is published this morning.\n"
    )
    result = runtime.complete([{"role": "user", "content": blob}], {})
    # Unknown window → default understand. Must NOT copy truth.yaml names/claims.
    assert result["project_candidates"] == []
    assert result["people_candidates"] == []
    texts = [str(claim["text"]) for claim in result["claims"]]
    assert "Onboarding Guide section 3 published" not in texts


def test_replay_summary_uses_index_citation() -> None:
    runtime = RecordingRuntime(path=RECORDINGS, mode="replay")
    result = runtime.complete(
        [{"role": "user", "content": "Write the daily narrative\n[c:deadbeef]"}],
        {},
    )
    text = str(result["paragraphs"][0]["text"])
    assert "[c:1]" in text
    assert "[c:deadbeef]" not in text
    assert "[c:<id>]" not in text


def test_replay_cedar_daily_recording_is_non_work() -> None:
    runtime = RecordingRuntime(path=RECORDINGS, mode="replay")
    result = runtime.complete(
        [{"role": "user", "content": "Window: cedar daily — harbor ferry\n1. civic news"}],
        {},
    )
    assert result["activity_type"] == "non_work"


def test_replay_matches_window_title_not_ocr_chrome() -> None:
    runtime = RecordingRuntime(path=RECORDINGS, mode="replay")
    result = runtime.complete(
        [
            {
                "role": "user",
                "content": (
                    "Window: onboarding guide — section 3\n"
                    "1. Cedar Daily  Pocket Calendar  Inbox\n"
                    "2. Section 3 of the Internal Onboarding Guide is published this morning.\n"
                ),
            }
        ],
        {},
    )
    assert result["activity_type"] != "non_work"
    texts = [str(claim["text"]) for claim in result["claims"]]
    assert any("onboarding guide section 3 published" in text.casefold() for text in texts)


def test_committed_recordings_are_labeled_hand_authored() -> None:
    import json

    payload = json.loads(RECORDINGS.read_text(encoding="utf-8"))
    assert payload["source"] == "hand-authored-imperfect"
    note = str(payload["note"]).lower()
    assert "truth.yaml" in note or "not copied" in note


def test_replay_embeddings_do_not_collapse_distinct_names() -> None:
    from watson.config.schema import DEFAULT_ENTITY_SIMILARITY
    from watson.intelligence.resolver import cosine_similarity

    runtime = RecordingRuntime(mode="replay")
    left, right = runtime.embed(["Priya Natarajan", "Tom Ashworth"])
    assert cosine_similarity(left, right) < DEFAULT_ENTITY_SIMILARITY


def test_record_mode_requires_inner_runtime() -> None:
    try:
        RecordingRuntime(mode="record")
    except RuntimeUnavailable as exc:
        assert "fake" in str(exc).lower() or "ollama" in str(exc).lower()
    else:
        raise AssertionError("record mode must not construct FakeRuntime")
