"""RecordingRuntime: replay default, no implicit FakeRuntime, extract/summary."""

from __future__ import annotations

from watson.intelligence.eval.recordings import RecordingRuntime
from watson.intelligence.runtime.types import RuntimeUnavailable


def test_replay_extracts_decisions_and_commitments_from_claim_lines() -> None:
    runtime = RecordingRuntime(mode="replay")
    blob = (
        "Extract cross-segment decisions\n"
        "1. [decision/observed] Agreed to move the Acme demo to 14 Nov\n"
        "2. [commitment/unconfirmed] Send revised pricing to Priya by Friday\n"
        "3. [activity/observed] Editing the appendix\n"
    )
    result = runtime.complete([{"role": "user", "content": blob}], {})
    assert result["decisions"] == [
        {"text": "Agreed to move the Acme demo to 14 Nov", "claim_indexes": [1]}
    ]
    assert result["commitments"] == [
        {
            "text": "Send revised pricing to Priya by Friday",
            "claim_indexes": [2],
            "status": "open",
        }
    ]


def test_replay_summary_cites_first_claim() -> None:
    runtime = RecordingRuntime(mode="replay")
    result = runtime.complete(
        [
            {
                "role": "user",
                "content": "Write the daily narrative\n[c:abc] (decision/observed) Demo",
            }
        ],
        {},
    )
    assert result["paragraphs"][0]["text"].endswith("[c:abc].")


def test_replay_noise_is_non_work() -> None:
    runtime = RecordingRuntime(mode="replay")
    result = runtime.complete(
        [{"role": "user", "content": "Window: Cedar Daily — Harbor ferry\n1. civic news"}],
        {},
    )
    assert result["activity_type"] == "non_work"
    assert result["claims"] == []


def test_replay_noise_is_window_scoped_not_nav_chrome() -> None:
    runtime = RecordingRuntime(mode="replay")
    blob = (
        "Window: Onboarding Guide — Section 3\n"
        "1. Onboarding | Cedar Daily | intranet\n"
        "2. Section 3 of the Internal Onboarding Guide is published this morning.\n"
    )
    result = runtime.complete([{"role": "user", "content": blob}], {})
    assert result["activity_type"] != "non_work"
    texts = [str(claim["text"]) for claim in result["claims"]]
    assert "Onboarding Guide section 3 published" in texts
    assert "Internal Onboarding Guide" in result["project_candidates"]


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
