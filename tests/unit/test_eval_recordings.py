"""RecordingRuntime: replay committed JSON only, no truth lexicon."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import pytest

from tests.replay_support import TEST_KEY
from watson.intelligence.eval.recordings import RecordingRuntime, ReplayMiss, prompt_hash
from watson.intelligence.eval.run import run_eval
from watson.intelligence.runtime.types import RuntimeUnavailable

REPO = Path(__file__).resolve().parents[2]
FIXTURES = REPO / "tests" / "fixtures"
RECORDINGS = FIXTURES / "eval" / "recordings.json"

_EXTRACT_PREFIX = (
    "# WATSON day extract — v1\n\nFrom the day's numbered claims and known entities, extract:\n"
)


def test_replay_uses_recording_without_augmentation() -> None:
    runtime = RecordingRuntime(path=RECORDINGS, mode="replay")
    blob = (
        "Window: Mystery App\n"
        "1. Internal Onboarding Guide and Priya Natarajan appear in chrome\n"
        "2. Section 3 of the Internal Onboarding Guide is published this morning.\n"
    )
    with pytest.raises(ReplayMiss, match=r"understanding key=[0-9a-f]{64}"):
        runtime.complete([{"role": "user", "content": blob}], {})


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


def _sha(blob: str, **kwargs: Any) -> str:
    return prompt_hash([{"role": "user", "content": blob}], schema={}, **kwargs)


def _complete(runtime: RecordingRuntime, blob: str) -> dict[str, Any]:
    return runtime.complete([{"role": "user", "content": blob}], {})


def _write_recordings(path: Path, rows: list[dict[str, Any]]) -> Path:
    path.write_text(
        json.dumps({"source": "test", "complete": rows, "embeddings": {}}, indent=2),
        encoding="utf-8",
    )
    return path


class _StubInner:
    name = "stub"

    def __init__(self, response: dict[str, Any]) -> None:
        self.response = response

    def complete(self, messages: list[dict[str, str]], schema: dict[str, Any]) -> dict[str, Any]:
        return dict(self.response)


def test_record_stores_full_prompt_hash_and_params(tmp_path: Path) -> None:
    """Surviving mutant: _match_key still stores Window / first 80 chars."""
    path = tmp_path / "rec.json"
    inner = _StubInner({"decisions": [], "commitments": [{"text": "A"}]})
    runtime = RecordingRuntime(path=path, inner=inner, mode="record")
    runtime.set_day("day_acme_q4", "2026-10-31")
    blob = _EXTRACT_PREFIX + "Claims:\n1. [commitment/observed] Send Priya the slide outline\n"
    result = _complete(runtime, blob)
    runtime.save()
    payload = json.loads(path.read_text(encoding="utf-8"))
    row = payload["complete"][0]
    assert result["commitments"][0]["text"] == "A"
    assert row["match"] == _sha(blob)
    assert row["match"] != blob[:80]
    assert row["day"] == "day_acme_q4"
    assert row["date"] == "2026-10-31"
    assert row["model"] == ""
    assert row["temperature"] == 0.0
    assert row["num_predict"] is None
    assert row["schema"] == {}


def test_prompt_hash_includes_roles_and_params() -> None:
    blob = _EXTRACT_PREFIX + "Claims:\n1. Alice\n"
    user = [{"role": "user", "content": blob}]
    system = [{"role": "system", "content": blob}]
    assert prompt_hash(user, schema={}) != prompt_hash(system, schema={})
    assert prompt_hash(user, schema={}) != prompt_hash(user, schema={"type": "object"})
    assert prompt_hash(user, schema={}, model="qwen2.5:7b") != prompt_hash(user, schema={})
    assert prompt_hash(user, schema={}, temperature=0.7) != prompt_hash(user, schema={})
    assert prompt_hash(user, schema={}, num_predict=256) != prompt_hash(user, schema={})


def test_two_days_with_different_claims_get_own_extracts(tmp_path: Path) -> None:
    """First-match on a shared prefix must not apply day A's extract to day B."""
    day_a = _EXTRACT_PREFIX + "Claims:\n1. [commitment/observed] Send Priya the slide outline\n"
    day_b = _EXTRACT_PREFIX + "Claims:\n1. [activity/observed] Checking the Lumen Desk inbox\n"
    path = _write_recordings(
        tmp_path / "hashed.json",
        [
            {
                "match": _sha(day_a),
                "response": {"commitments": [{"text": "Send Priya the slide outline"}]},
            },
            {"match": _sha(day_b), "response": {"commitments": []}},
        ],
    )
    runtime = RecordingRuntime(path=path, mode="replay")
    assert _complete(runtime, day_a)["commitments"][0]["text"] == "Send Priya the slide outline"
    assert _complete(runtime, day_b)["commitments"] == []


def test_legacy_out_of_order_uses_day_not_call_order(tmp_path: Path) -> None:
    """Replaying day B first must not receive day A's project-X extract."""
    prefix = _EXTRACT_PREFIX[:80]
    day_b_prompt = _EXTRACT_PREFIX + "Claims:\n(none)\n"
    path = _write_recordings(
        tmp_path / "legacy.json",
        [
            {
                "match": prefix,
                "day": "day_acme_q4",
                "response": {"commitments": [{"text": "Commit to complete project X by Q4"}]},
            },
            {"match": prefix, "day": "day_near_empty", "response": {"commitments": []}},
        ],
    )
    runtime = RecordingRuntime(path=path, mode="replay")
    runtime.set_day("day_near_empty", "2026-11-06")
    assert _complete(runtime, day_b_prompt)["commitments"] == []


def test_legacy_exhausted_rows_are_a_miss(tmp_path: Path) -> None:
    """Surviving mutant: exhausted-reuses-last (day C must not get day B)."""
    prefix = _EXTRACT_PREFIX[:80]
    prompt = _EXTRACT_PREFIX + "Claims:\n1. Alice\n"
    path = _write_recordings(
        tmp_path / "exhausted.json",
        [
            {
                "match": prefix,
                "day": "day_acme_q4",
                "response": {"commitments": [{"text": "project X"}]},
            },
            {
                "match": prefix,
                "day": "day_near_empty",
                "response": {"commitments": [{"text": "last-row"}]},
            },
        ],
    )
    runtime = RecordingRuntime(path=path, mode="replay")
    runtime.set_day("day_meeting_heavy", "2026-11-04")
    with pytest.raises(ReplayMiss, match=r"day_extract key=[0-9a-f]{64}"):
        _complete(runtime, prompt)


def test_legacy_window_is_not_consumed(tmp_path: Path) -> None:
    """Surviving mutant: window-consumes (revisit must still match)."""
    path = _write_recordings(
        tmp_path / "window.json",
        [{"match": "cedar daily", "response": {"activity_type": "non_work", "claims": []}}],
    )
    runtime = RecordingRuntime(path=path, mode="replay")
    blob = "Window: cedar daily — harbor ferry\n1. civic news"
    assert _complete(runtime, blob)["activity_type"] == "non_work"
    assert _complete(runtime, blob)["activity_type"] == "non_work"


def test_legacy_window_match_is_casefold(tmp_path: Path) -> None:
    """Surviving mutant: no-casefold."""
    path = _write_recordings(
        tmp_path / "case.json",
        [{"match": "cedar daily", "response": {"activity_type": "non_work", "claims": []}}],
    )
    runtime = RecordingRuntime(path=path, mode="replay")
    assert (
        _complete(runtime, "Window: CEDAR DAILY — harbor ferry\n1. civic news")["activity_type"]
        == "non_work"
    )


def test_replay_miss_including_trailing_space(tmp_path: Path) -> None:
    blob = _EXTRACT_PREFIX + "Claims:\n1. Alice\n"
    path = _write_recordings(
        tmp_path / "miss.json",
        [{"match": _sha(blob), "response": {"commitments": [{"text": "hit"}]}}],
    )
    runtime = RecordingRuntime(path=path, mode="replay")
    assert _complete(runtime, blob)["commitments"][0]["text"] == "hit"
    with pytest.raises(ReplayMiss, match=r"day_extract key=[0-9a-f]{64}") as raised:
        _complete(runtime, blob + " ")
    assert "Alice" not in str(raised.value)
    assert blob not in str(raised.value)


def test_lenient_miss_warns_and_returns_default(tmp_path: Path, caplog: Any) -> None:
    path = _write_recordings(tmp_path / "lenient.json", [])
    runtime = RecordingRuntime(path=path, mode="replay", strict=False)
    with caplog.at_level(logging.WARNING):
        result = _complete(runtime, "Window: Mystery App\n1. nothing")
    assert result["project_candidates"] == []
    assert any("lenient" in rec.getMessage().casefold() for rec in caplog.records)


def test_legacy_content_key_reuse_does_not_require_unique_count(tmp_path: Path) -> None:
    """Surviving mutant: cluster keys appear twice (two consolidate passes)."""
    key = "Priya Natarajan is communicating about moving a demo date"
    path = _write_recordings(
        tmp_path / "cluster.json",
        [
            {"match": key, "response": {"activities": [{"title": "Pricing"}]}},
            {"match": key, "response": {"activities": [{"title": "Pricing"}]}},
        ],
    )
    runtime = RecordingRuntime(path=path, mode="replay")
    blob = f"Cluster these observations into activities.\n\n1. {key} and pricing\n"
    assert _complete(runtime, blob)["activities"][0]["title"] == "Pricing"
    assert _complete(runtime, blob)["activities"][0]["title"] == "Pricing"


def test_legacy_shared_prefix_follows_neighbor_window_not_slot_order(
    tmp_path: Path,
) -> None:
    """Four extract prefixes are 2× acme + 2× meeting, not one per first four days."""
    prefix = _EXTRACT_PREFIX[:80]
    path = _write_recordings(
        tmp_path / "neighbors.json",
        [
            {
                "match": "re: acme q4 proposal — priya natarajan",
                "response": {"activity_type": "communication", "claims": []},
            },
            {"match": prefix, "response": {"commitments": [{"text": "acme-row"}]}},
            {"match": prefix, "response": {"commitments": [{"text": "acme-row"}]}},
            {
                "match": "lumen room — delivery standup",
                "response": {"activity_type": "communication", "claims": []},
            },
            {"match": prefix, "response": {"commitments": [{"text": "meeting-row"}]}},
            {"match": prefix, "response": {"commitments": [{"text": "meeting-row"}]}},
        ],
    )
    runtime = RecordingRuntime(path=path, mode="replay")
    runtime.set_day("day_meeting_heavy", "2026-11-04")
    meeting = _EXTRACT_PREFIX + "Claims:\n1. standup\n"
    assert _complete(runtime, meeting)["commitments"][0]["text"] == "meeting-row"
    runtime.set_day("day_near_empty", "2026-11-06")
    with pytest.raises(ReplayMiss, match=r"day_extract key=[0-9a-f]{64}"):
        _complete(runtime, _EXTRACT_PREFIX + "Claims:\n1. empty\n")


def test_legacy_unique_extract_still_applies_to_every_day(tmp_path: Path) -> None:
    """CI recordings have one extract row; every day may reuse that unique key."""
    path = _write_recordings(
        tmp_path / "unique.json",
        [
            {
                "match": "Extract cross-segment",
                "response": {"entity_deltas": [], "decisions": [], "commitments": []},
            }
        ],
    )
    runtime = RecordingRuntime(path=path, mode="replay")
    day_a = "Extract cross-segment decisions\nClaims:\n1. Alice\n"
    day_b = "Extract cross-segment decisions\nClaims:\n1. Bob\n"
    assert _complete(runtime, day_a)["commitments"] == []
    assert _complete(runtime, day_b)["commitments"] == []


def test_legacy_format_file_still_loads() -> None:
    """Committed CI recordings keep their window-title and phrase keys."""
    runtime = RecordingRuntime(path=RECORDINGS, mode="replay")
    result = _complete(runtime, "Window: cedar daily — harbor ferry\n1. civic news")
    assert result["activity_type"] == "non_work"
    narrative = _complete(runtime, "Write the daily narrative\n[c:deadbeef]")
    assert "[c:1]" in str(narrative["paragraphs"][0]["text"])


def test_legacy_fallback_emits_a_warning(tmp_path: Path, caplog: Any) -> None:
    path = _write_recordings(
        tmp_path / "warn.json",
        [{"match": "Extract cross-segment", "response": {"commitments": []}}],
    )
    runtime = RecordingRuntime(path=path, mode="replay")
    with caplog.at_level(logging.WARNING):
        _complete(runtime, "Extract cross-segment decisions\nClaims:\n1. Alice\n")
    assert any("legacy" in rec.getMessage().casefold() for rec in caplog.records)
    assert any("day/date" in rec.getMessage().casefold() for rec in caplog.records)


def test_replay_same_recordings_twice_gives_identical_metrics(
    tmp_path: Path, key_provider: object
) -> None:
    """A shared prefix must not make a second run drift (qwen7b 0.00 vs 0.20)."""
    first = run_eval(
        fixtures_root=FIXTURES,
        names=("day_acme_q4", "day_near_empty"),
        mode="replay",
        runtime_name="replay",
        recordings=RECORDINGS,
        out_dir=tmp_path / "once",
        key=TEST_KEY,
    )
    second = run_eval(
        fixtures_root=FIXTURES,
        names=("day_acme_q4", "day_near_empty"),
        mode="replay",
        runtime_name="replay",
        recordings=RECORDINGS,
        out_dir=tmp_path / "twice",
        key=TEST_KEY,
    )
    assert first.aggregate == second.aggregate
    assert first.pipeline_passed is True
    assert second.pipeline_passed is True


def test_exact_prompt_hash_wins_over_legacy_prefix(tmp_path: Path) -> None:
    blob = _EXTRACT_PREFIX + "Claims:\n1. [commitment/observed] Send Priya the slide outline\n"
    path = _write_recordings(
        tmp_path / "prefer.json",
        [
            {
                "match": blob[:80],
                "response": {"commitments": [{"text": "legacy-prefix"}]},
            },
            {
                "match": _sha(blob),
                "response": {"commitments": [{"text": "hashed"}]},
            },
        ],
    )
    runtime = RecordingRuntime(path=path, mode="replay")
    assert _complete(runtime, blob)["commitments"][0]["text"] == "hashed"
