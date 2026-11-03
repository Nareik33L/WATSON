"""RecordingRuntime: replay committed JSON only, no truth lexicon."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import pytest

from tests.replay_support import TEST_KEY
from watson.intelligence.eval.recordings import (
    RecordingRuntime,
    ReplayMiss,
    ReplayParamsError,
    _is_window_key,
    _key_matches_kind,
    prompt_hash,
)
from watson.intelligence.eval.run import run_eval
from watson.intelligence.runtime.types import RuntimeUnavailable
from watson.memory.ids import bind_issued, reset_issued

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


def _write_v1(
    path: Path,
    rows: list[dict[str, Any]],
    *,
    model: str = "qwen2.5:7b",
    temperature: float = 0.0,
) -> Path:
    path.write_text(
        json.dumps(
            {
                "format": "prompt-hash-v1",
                "model": model,
                "temperature": temperature,
                "source": "test",
                "complete": rows,
                "embeddings": {},
            },
            indent=2,
        ),
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


def test_prompt_hash_uses_every_message_not_just_the_first() -> None:
    """Surviving mutant: hash-first-message."""
    left = [{"role": "system", "content": "S"}, {"role": "user", "content": "U1"}]
    right = [{"role": "system", "content": "S"}, {"role": "user", "content": "U2"}]
    assert prompt_hash(left, schema={}) != prompt_hash(right, schema={})
    assert prompt_hash(left, schema={}) != prompt_hash(
        [{"role": "user", "content": "U1"}], schema={}
    )


def test_prompt_hash_strips_run_scoped_ids() -> None:
    send_a = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    send_b = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
    left = f"Claims:\n[c:{send_a}] (commitment/observed) Send it\n"
    right = f"Claims:\n[c:{send_b}] (commitment/observed) Send it\n"
    assert prompt_hash(
        [{"role": "user", "content": left}], schema={}, run_ids=[send_a]
    ) == prompt_hash([{"role": "user", "content": right}], schema={}, run_ids=[send_b])


def test_prompt_hash_run_id_ordinals_follow_appearance_not_sort() -> None:
    """First-seen ordinals, not sorted UUID order. Swapped IDs in the same slots match."""
    send = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    edit = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
    left = f"note {send} then {edit}"
    right = f"note {edit} then {send}"
    assert prompt_hash(
        [{"role": "user", "content": left}], schema={}, run_ids=[send, edit]
    ) == prompt_hash([{"role": "user", "content": right}], schema={}, run_ids=[send, edit])


def test_prompt_hash_keeps_claim_prompt_order() -> None:
    """Sorted order must not be the key — prompt order is."""
    send = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    edit = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
    prompt_order = (
        "Claims:\n"
        f"1. [c:{send}] (commitment/observed) Send it\n"
        f"2. [c:{edit}] (activity/observed) Edit doc\n"
    )
    swapped = (
        "Claims:\n"
        f"1. [c:{edit}] (activity/observed) Edit doc\n"
        f"2. [c:{send}] (commitment/observed) Send it\n"
    )
    assert prompt_hash(
        [{"role": "user", "content": prompt_order}], schema={}, run_ids=[send, edit]
    ) != prompt_hash([{"role": "user", "content": swapped}], schema={}, run_ids=[send, edit])


def test_prompt_hash_repeated_ids_keep_distinct_ordinals() -> None:
    """A,B,A and A,B,B must hash differently. Mapping every run id to '1' collapses them."""
    first = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    second = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
    aba = f"note {first} then {second} then {first}"
    abb = f"note {first} then {second} then {second}"
    assert prompt_hash(
        [{"role": "user", "content": aba}], schema={}, run_ids=[first, second]
    ) != prompt_hash([{"role": "user", "content": abb}], schema={}, run_ids=[first, second])


def test_prompt_hash_leaves_fixture_uuid() -> None:
    """Fixture UUIDs stay verbatim. Only run-issued IDs become ordinals."""
    fixture_id = "ffffffff-ffff-4fff-8fff-ffffffffffff"
    run_id = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    other_id = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
    stamp = "2026-10-31T12:00:00Z"
    left = f"Window: note {fixture_id} at {stamp}\n[c:{run_id}]"
    right = f"Window: note {other_id} at {stamp}\n[c:{run_id}]"
    assert prompt_hash(
        [{"role": "user", "content": left}], schema={}, run_ids=[run_id]
    ) != prompt_hash([{"role": "user", "content": right}], schema={}, run_ids=[run_id])
    same_fixture = f"Window: note {fixture_id} at {stamp}\n[c:{other_id}]"
    assert prompt_hash(
        [{"role": "user", "content": left}], schema={}, run_ids=[run_id]
    ) == prompt_hash(
        [{"role": "user", "content": same_fixture}],
        schema={},
        run_ids=[other_id],
    )


def test_prompt_hash_leaves_fixture_timestamp() -> None:
    """Unregistered timestamps stay verbatim. Same fixture UUID, times differ."""
    fixture_id = "ffffffff-ffff-4fff-8fff-ffffffffffff"
    run_id = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    left = f"Window: note {fixture_id} at 2026-10-31T12:00:00Z\n[c:{run_id}]"
    right = f"Window: note {fixture_id} at 2026-10-31T12:00:01Z\n[c:{run_id}]"
    assert prompt_hash(
        [{"role": "user", "content": left}], schema={}, run_ids=[run_id]
    ) != prompt_hash([{"role": "user", "content": right}], schema={}, run_ids=[run_id])


def test_replay_maps_ordinal_cites_to_prompt_order_not_sorted(tmp_path: Path) -> None:
    """Surviving mutant: no-ordinalize / sort-then-cite. Edit < Send alphabetically."""
    send = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    edit = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
    path = tmp_path / "cites.json"

    class _CiteInner:
        name = "ollama"
        llm_model = "qwen2.5:7b"
        temperature = 0.0

        def complete(
            self,
            messages: list[dict[str, str]],
            schema: dict[str, Any],
            *,
            num_predict: int | None = None,
        ) -> dict[str, Any]:
            del schema, num_predict
            blob = "\n".join(item["content"] for item in messages)
            cited = edit if edit in blob else send
            return {"paragraphs": [{"text": f"Edited the doc [c:{cited}].", "claim_ids": [cited]}]}

    prompt = (
        "Write the daily narrative\nClaims:\n"
        f"1. [c:{send}] (commitment/observed) Send it\n"
        f"2. [c:{edit}] (activity/observed) Edit doc\n"
    )
    token = bind_issued([send, edit], [])
    try:
        runtime = RecordingRuntime(path=path, inner=_CiteInner(), mode="record")
        recorded = runtime.complete([{"role": "user", "content": prompt}], {})
        runtime.save()
    finally:
        reset_issued(token)
    assert edit in str(recorded["paragraphs"][0]["text"])
    stored = json.loads(path.read_text(encoding="utf-8"))["complete"][0]["response"]
    assert "[c:2]" in str(stored["paragraphs"][0]["text"])
    assert edit not in str(stored["paragraphs"][0]["text"])

    send2 = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
    edit2 = "dddddddd-dddd-4ddd-8ddd-dddddddddddd"
    replay_prompt = (
        "Write the daily narrative\nClaims:\n"
        f"1. [c:{send2}] (commitment/observed) Send it\n"
        f"2. [c:{edit2}] (activity/observed) Edit doc\n"
    )
    token = bind_issued([send2, edit2], [])
    try:
        replay = RecordingRuntime(path=path, mode="replay")
        result = replay.complete([{"role": "user", "content": replay_prompt}], {})
    finally:
        reset_issued(token)
    text = str(result["paragraphs"][0]["text"])
    assert edit2 in text
    assert send2 not in text
    assert result["paragraphs"][0]["claim_ids"] == [edit2]
    assert replay.miss_count == 0


class _NamedInner:
    name = "ollama"
    llm_model = "qwen2.5:7b"
    temperature = 0.0

    def complete(
        self,
        messages: list[dict[str, str]],
        schema: dict[str, Any],
        *,
        num_predict: int | None = None,
    ) -> dict[str, Any]:
        del messages, schema, num_predict
        return {"commitments": [{"text": "named"}]}


def test_replay_uses_recorded_model_when_inner_is_missing(tmp_path: Path) -> None:
    """Surviving mutant: model-const (replay hashed empty model / temp 0)."""
    path = tmp_path / "named.json"
    runtime = RecordingRuntime(path=path, inner=_NamedInner(), mode="record")
    blob = _EXTRACT_PREFIX + "Claims:\n1. Alice\n"
    _complete(runtime, blob)
    runtime.save()
    row = json.loads(path.read_text(encoding="utf-8"))["complete"][0]
    assert row["model"] == "qwen2.5:7b"
    assert row["temperature"] == 0.0
    replay = RecordingRuntime(path=path, mode="replay")
    assert _complete(replay, blob)["commitments"][0]["text"] == "named"
    assert replay.miss_count == 0


def test_legacy_matches_by_prompt_date_without_set_day(tmp_path: Path) -> None:
    """Surviving mutant: run-no-set_day."""
    prefix = _EXTRACT_PREFIX[:80]
    path = _write_recordings(
        tmp_path / "dates.json",
        [
            {
                "match": prefix,
                "day": "day_acme_q4",
                "date": "2026-10-31",
                "response": {"commitments": [{"text": "acme"}]},
            },
            {
                "match": prefix,
                "day": "day_meeting_heavy",
                "date": "2026-11-04",
                "response": {"commitments": [{"text": "meeting"}]},
            },
        ],
    )
    runtime = RecordingRuntime(path=path, mode="replay")
    blob = _EXTRACT_PREFIX + "Working day 2026-11-04\nClaims:\n1. standup\n"
    assert _complete(runtime, blob)["commitments"][0]["text"] == "meeting"


def test_record_replay_round_trip_with_real_model_name(
    tmp_path: Path, key_provider: object, monkeypatch: object
) -> None:
    """Record with qwen2.5:7b, different claim IDs on two runs, then replay."""
    from watson.intelligence.eval import run as run_mod
    from watson.intelligence.runtime.fake import FakeRuntime

    class _NamedFake:
        name = "ollama"
        llm_model = "qwen2.5:7b"
        temperature = 0.0

        def __init__(self) -> None:
            self._fake = FakeRuntime(recordings=RECORDINGS)

        def complete(
            self,
            messages: list[dict[str, str]],
            schema: dict[str, Any],
            *,
            num_predict: int | None = None,
        ) -> dict[str, Any]:
            return self._fake.complete(messages, schema, num_predict=num_predict)

        def embed(self, texts: list[str]) -> list[list[float]]:
            return self._fake.embed(texts)

        def end_phase(self) -> None:
            self._fake.end_phase()

    rec1 = tmp_path / "round1.json"
    rec2 = tmp_path / "round2.json"

    def build(
        *, mode: str, runtime_name: str, recordings: Path, config: object
    ) -> RecordingRuntime:
        del runtime_name, config
        if mode == "record":
            return RecordingRuntime(path=recordings, inner=_NamedFake(), mode="record")
        return RecordingRuntime(path=recordings, mode="replay")

    monkeypatch.setattr(run_mod, "build_eval_runtime", build)
    names = ("day_acme_q4", "day_near_empty")
    run_eval(
        fixtures_root=FIXTURES,
        names=names,
        mode="record",
        runtime_name="ollama",
        recordings=rec1,
        out_dir=tmp_path / "record-a",
        key=TEST_KEY,
    )
    run_eval(
        fixtures_root=FIXTURES,
        names=names,
        mode="record",
        runtime_name="ollama",
        recordings=rec2,
        out_dir=tmp_path / "record-b",
        key=TEST_KEY,
    )
    keys1 = [row["match"] for row in json.loads(rec1.read_text(encoding="utf-8"))["complete"]]
    keys2 = [row["match"] for row in json.loads(rec2.read_text(encoding="utf-8"))["complete"]]
    assert keys1 == keys2
    assert keys1
    models = {row.get("model") for row in json.loads(rec1.read_text(encoding="utf-8"))["complete"]}
    assert models == {"qwen2.5:7b"}
    first = run_eval(
        fixtures_root=FIXTURES,
        names=names,
        mode="replay",
        runtime_name="replay",
        recordings=rec1,
        out_dir=tmp_path / "replay-a",
        key=TEST_KEY,
    )
    second = run_eval(
        fixtures_root=FIXTURES,
        names=names,
        mode="replay",
        runtime_name="replay",
        recordings=rec1,
        out_dir=tmp_path / "replay-b",
        key=TEST_KEY,
    )
    assert first.failure_reason is None
    assert first.pipeline_passed is True
    assert second.pipeline_passed is True
    assert first.aggregate == second.aggregate
    assert sum(day.citation_count for day in first.days) > 0
    assert all(not day.error for day in first.days)


def _drop_match(tmp_path: Path, needle: str) -> Path:
    payload = json.loads(RECORDINGS.read_text(encoding="utf-8"))
    payload["complete"] = [
        row for row in payload["complete"] if needle not in str(row.get("match") or "").casefold()
    ]
    path = tmp_path / f"dropped-{needle.replace(' ', '-')}.json"
    path.write_text(json.dumps(payload) + "\n", encoding="utf-8")
    return path


def test_dropped_recording_row_fails_eval(tmp_path: Path, key_provider: object) -> None:
    path = _drop_match(tmp_path, "cedar daily")
    report = run_eval(
        fixtures_root=FIXTURES,
        names=("day_acme_q4",),
        mode="replay",
        runtime_name="replay",
        recordings=path,
        out_dir=tmp_path / "dropped-run",
        key=TEST_KEY,
    )
    assert report.pipeline_passed is False
    reason = report.failure_reason or ""
    assert "ReplayMiss" in reason
    assert "understanding" in reason
    assert "key=" in reason
    assert (0 if report.pipeline_passed else 1) != 0


def test_dropped_extract_row_fails_eval(tmp_path: Path, key_provider: object) -> None:
    path = _drop_match(tmp_path, "extract cross-segment")
    report = run_eval(
        fixtures_root=FIXTURES,
        names=("day_acme_q4",),
        mode="replay",
        runtime_name="replay",
        recordings=path,
        out_dir=tmp_path / "dropped-extract",
        key=TEST_KEY,
    )
    assert report.pipeline_passed is False
    reason = report.failure_reason or ""
    assert "ReplayMiss" in reason
    assert "day_extract" in reason
    assert (0 if report.pipeline_passed else 1) != 0


def test_dropped_summary_row_fails_eval(tmp_path: Path, key_provider: object) -> None:
    path = _drop_match(tmp_path, "write the daily narrative")
    report = run_eval(
        fixtures_root=FIXTURES,
        names=("day_acme_q4",),
        mode="replay",
        runtime_name="replay",
        recordings=path,
        out_dir=tmp_path / "dropped-summary",
        key=TEST_KEY,
    )
    assert report.pipeline_passed is False
    reason = report.failure_reason or ""
    assert "ReplayMiss" in reason
    assert "daily_summary" in reason
    assert (0 if report.pipeline_passed else 1) != 0


def test_new_format_rejects_different_model(tmp_path: Path) -> None:
    path = tmp_path / "named.json"
    runtime = RecordingRuntime(path=path, inner=_NamedInner(), mode="record")
    _complete(runtime, _EXTRACT_PREFIX + "Claims:\n1. Alice\n")
    runtime.save()
    with pytest.raises(ReplayParamsError, match="qwen2.5:7b"):
        RecordingRuntime(path=path, mode="replay", expected_model="llama3.1:8b")


def test_replay_eval_rejects_configured_model_mismatch(
    tmp_path: Path, key_provider: object
) -> None:
    path = tmp_path / "named.json"
    runtime = RecordingRuntime(path=path, inner=_NamedInner(), mode="record")
    _complete(runtime, _EXTRACT_PREFIX + "Claims:\n1. Alice\n")
    runtime.save()

    class _Intel:
        llm_model = "llama3.1:8b"

    class _Config:
        intelligence = _Intel()

    report = run_eval(
        fixtures_root=FIXTURES,
        names=("day_near_empty",),
        mode="replay",
        runtime_name="replay",
        recordings=path,
        out_dir=tmp_path / "mismatch",
        key=TEST_KEY,
        config=_Config(),
    )
    assert report.pipeline_passed is False
    reason = report.failure_reason or ""
    assert "ReplayParamsError" in reason
    assert "qwen2.5:7b" in reason
    assert "llama3.1:8b" in reason


def test_report_names_recorded_model(
    tmp_path: Path, key_provider: object, monkeypatch: object
) -> None:
    from watson.intelligence.eval import run as run_mod
    from watson.intelligence.runtime.fake import FakeRuntime

    class _NamedFake:
        name = "ollama"
        llm_model = "qwen2.5:7b"
        temperature = 0.0

        def __init__(self) -> None:
            self._fake = FakeRuntime(recordings=RECORDINGS)

        def complete(
            self,
            messages: list[dict[str, str]],
            schema: dict[str, Any],
            *,
            num_predict: int | None = None,
        ) -> dict[str, Any]:
            return self._fake.complete(messages, schema, num_predict=num_predict)

        def embed(self, texts: list[str]) -> list[list[float]]:
            return self._fake.embed(texts)

        def end_phase(self) -> None:
            self._fake.end_phase()

    rec = tmp_path / "named.json"

    def build(
        *, mode: str, runtime_name: str, recordings: Path, config: object
    ) -> RecordingRuntime:
        del runtime_name, config
        if mode == "record":
            return RecordingRuntime(path=recordings, inner=_NamedFake(), mode="record")
        return RecordingRuntime(path=recordings, mode="replay")

    monkeypatch.setattr(run_mod, "build_eval_runtime", build)
    run_eval(
        fixtures_root=FIXTURES,
        names=("day_near_empty",),
        mode="record",
        runtime_name="ollama",
        recordings=rec,
        out_dir=tmp_path / "record",
        key=TEST_KEY,
    )
    report = run_eval(
        fixtures_root=FIXTURES,
        names=("day_near_empty",),
        mode="replay",
        runtime_name="replay",
        recordings=rec,
        out_dir=tmp_path / "replay",
        key=TEST_KEY,
    )
    assert report.model == "qwen2.5:7b"
    markdown = (tmp_path / "replay" / "report.md").read_text(encoding="utf-8")
    payload = json.loads((tmp_path / "replay" / "report.json").read_text(encoding="utf-8"))
    assert "qwen2.5:7b" in markdown
    assert payload["model"] == "qwen2.5:7b"


def test_miss_increments_count_logs_and_sets_last_miss(tmp_path: Path, caplog: Any) -> None:
    """Surviving mutants: miss_count not incremented / no last_miss / no miss log."""
    path = _write_recordings(tmp_path / "empty.json", [])
    runtime = RecordingRuntime(path=path, mode="replay")
    with caplog.at_level(logging.ERROR):
        with pytest.raises(ReplayMiss, match=r"understanding key=[0-9a-f]{64}"):
            _complete(runtime, "Window: Mystery App\n1. nothing")
    assert runtime.miss_count == 1
    assert runtime.last_miss is not None
    assert runtime.last_miss.startswith("ReplayMiss:")
    assert "understanding" in runtime.last_miss
    assert "key=" in runtime.last_miss
    assert any("replay miss for understanding" in rec.getMessage() for rec in caplog.records)


def test_lenient_miss_count_fails_pipeline_without_exception(
    tmp_path: Path, key_provider: object, monkeypatch: object
) -> None:
    """Surviving mutant: miss_count gate ignored. Deterministic, no lease wait."""
    from watson.intelligence.eval import run as run_mod
    from watson.intelligence.eval.run import DayEval
    from watson.intelligence.eval.thresholds import load_thresholds

    path = _write_recordings(tmp_path / "empty.json", [])

    def build(
        *, mode: str, runtime_name: str, recordings: Path, config: object
    ) -> RecordingRuntime:
        del mode, runtime_name, config
        return RecordingRuntime(path=recordings, mode="replay", strict=False)

    metrics = {name: 1.0 for name in load_thresholds()}
    metrics["unsupported_achievements"] = 0.0
    metrics["duplicate_observation_rate"] = 0.0

    def fake_one(
        fixture: Path, *, runtime: Any, out_dir: Path, key: bytes, now_fn: object
    ) -> DayEval:
        del out_dir, key, now_fn
        runtime.complete([{"role": "user", "content": "Window: Mystery App\n1. nothing"}], {})
        return DayEval(
            name=fixture.name,
            date="2026-11-06",
            metrics=metrics,
            derived_first={"summaries": 1},
            derived_second={"summaries": 1},
            summary_present=True,
            identical_counts=True,
            narrative_nonempty=True,
            citation_count=1,
            claims=1,
        )

    monkeypatch.setattr(run_mod, "build_eval_runtime", build)
    monkeypatch.setattr(run_mod, "_eval_one", fake_one)
    report = run_eval(
        fixtures_root=FIXTURES,
        names=("day_near_empty",),
        mode="replay",
        runtime_name="replay",
        recordings=path,
        out_dir=tmp_path / "lenient-miss",
        key=TEST_KEY,
    )
    assert report.pipeline_passed is False
    reason = report.failure_reason or ""
    assert "ReplayMiss" in reason
    assert "understanding" in reason
    assert "key=" in reason


def test_prompt_hash_leaves_unregistered_iso_date() -> None:
    """Surviving mutant: over-broad date normalisation (YYYY-MM-DD without time)."""
    run_id = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    left = f"Working day 2026-10-31\n[c:{run_id}]"
    right = f"Working day 2026-11-04\n[c:{run_id}]"
    assert prompt_hash(
        [{"role": "user", "content": left}], schema={}, run_ids=[run_id]
    ) != prompt_hash([{"role": "user", "content": right}], schema={}, run_ids=[run_id])


def test_prompt_hash_strips_registered_run_stamps() -> None:
    """Surviving mutant: stamps not registered via iso_now/note_stamp."""
    from datetime import UTC, datetime

    from watson.memory.db import iso_now
    from watson.memory.ids import issued_stamps

    token = bind_issued()
    try:
        first = iso_now(datetime(2026, 10, 31, 12, 0, 0, tzinfo=UTC))
        second = iso_now(datetime(2026, 10, 31, 13, 0, 0, tzinfo=UTC))
        assert first in issued_stamps()
        assert second in issued_stamps()
        run_id = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
        left = f"Claims at {first}\n[c:{run_id}]"
        right = f"Claims at {second}\n[c:{run_id}]"
        assert prompt_hash(
            [{"role": "user", "content": left}], schema={}, run_ids=[run_id]
        ) == prompt_hash([{"role": "user", "content": right}], schema={}, run_ids=[run_id])
    finally:
        reset_issued(token)


def test_acme_clustering_key_is_not_a_window_key() -> None:
    key = "Reading mail about the Acme Q4 proposal."
    assert not _is_window_key(key)
    assert _key_matches_kind(key, "clustering")
    assert _is_window_key("cedar daily")
    assert not _key_matches_kind("cedar daily", "clustering")


def test_clustering_does_not_serve_window_key_row(tmp_path: Path) -> None:
    path = _write_recordings(
        tmp_path / "cluster-kind.json",
        [
            {
                "match": "cedar daily",
                "response": {"deals": [{"title": "From window", "observation_indexes": [1]}]},
            }
        ],
    )
    runtime = RecordingRuntime(path=path, mode="replay")
    with pytest.raises(ReplayMiss, match=r"clustering key=[0-9a-f]{64}"):
        _complete(runtime, "Cluster these observations into deals.\n1. cedar daily leftover\n")


def test_clustering_prefers_non_window_phrase_over_catchall(tmp_path: Path) -> None:
    path = _write_recordings(
        tmp_path / "cluster-phrase.json",
        [
            {
                "match": "Reading mail about the Acme Q4 proposal.",
                "response": {
                    "deals": [{"title": "Acme Q4 proposal", "observation_indexes": [1, 2, 3]}]
                },
            },
            {
                "match": "Cluster these observations",
                "response": {"deals": [{"title": "Day work", "observation_indexes": [1]}]},
            },
            {
                "match": "cedar daily",
                "response": {"deals": [{"title": "From window", "observation_indexes": [1]}]},
            },
        ],
    )
    runtime = RecordingRuntime(path=path, mode="replay")
    result = _complete(
        runtime,
        "Cluster these observations into deals.\n"
        "1. Reading mail about the Acme Q4 proposal. (deal candidates: Acme)\n",
    )
    assert result["deals"][0]["title"] == "Acme Q4 proposal"


def test_extract_does_not_consume_window_row(tmp_path: Path) -> None:
    """Surviving mutant: extract kind 'any' serves understand window rows."""
    path = _write_recordings(
        tmp_path / "extract-kind.json",
        [{"match": "cedar daily", "response": {"activity_type": "non_work", "claims": []}}],
    )
    runtime = RecordingRuntime(path=path, mode="replay")
    with pytest.raises(ReplayMiss, match=r"day_extract key=[0-9a-f]{64}"):
        _complete(runtime, _EXTRACT_PREFIX + "Claims:\n1. cedar daily leftover\n")


def test_consolidation_structured_reraises_replay_miss(tmp_path: Path) -> None:
    """Surviving mutant: consolidation swallows ReplayMiss into ConsolidationError."""
    from watson.intelligence.schemas.consolidation import DayExtractOutput
    from watson.pipeline.consolidation import DAY_EXTRACT_JSON_SCHEMA, _structured

    path = _write_recordings(tmp_path / "empty.json", [])
    runtime = RecordingRuntime(path=path, mode="replay")
    with pytest.raises(ReplayMiss, match=r"day_extract key=[0-9a-f]{64}"):
        _structured(
            runtime,
            [{"role": "user", "content": _EXTRACT_PREFIX + "Claims:\n1. Alice\n"}],
            DayExtractOutput,
            DAY_EXTRACT_JSON_SCHEMA,
            "day extract",
        )


def test_understanding_does_not_consume_extract_row(tmp_path: Path) -> None:
    """Surviving mutant: understanding kind 'any' serves extract rows."""
    path = _write_recordings(
        tmp_path / "kind.json",
        [
            {
                "match": "[commitment] Send the slides",
                "response": {"commitments": [{"text": "extract-only"}]},
            }
        ],
    )
    runtime = RecordingRuntime(path=path, mode="replay")
    with pytest.raises(ReplayMiss, match=r"understanding key=[0-9a-f]{64}"):
        _complete(runtime, "Window: Inbox — [commitment] Send the slides\n1. something")


def test_new_format_does_not_use_legacy_lookup(tmp_path: Path) -> None:
    """Surviving mutant: legacy lookup still runs on prompt-hash-v1 files."""
    blob = "Window: cedar daily — harbor ferry\n1. civic news"
    path = _write_v1(
        tmp_path / "new.json",
        [{"match": "cedar daily", "response": {"activity_type": "non_work", "claims": []}}],
    )
    runtime = RecordingRuntime(path=path, mode="replay")
    with pytest.raises(ReplayMiss, match=r"understanding key=[0-9a-f]{64}"):
        _complete(runtime, blob)


def test_new_format_rejects_temperature_mismatch_without_inner(tmp_path: Path) -> None:
    """Surviving mutant: temperature reject gated on inner is not None."""
    path = tmp_path / "named.json"
    runtime = RecordingRuntime(path=path, inner=_NamedInner(), mode="record")
    _complete(runtime, _EXTRACT_PREFIX + "Claims:\n1. Alice\n")
    runtime.save()
    with pytest.raises(ReplayParamsError, match="temperature"):
        RecordingRuntime(path=path, mode="replay", expected_temperature=0.7)


def test_build_eval_runtime_passes_expected_temperature() -> None:
    from watson.intelligence.eval.run import build_eval_runtime

    class _Intel:
        llm_model = "qwen2.5:7b"
        temperature = 0.7

    class _Config:
        intelligence = _Intel()

    runtime = build_eval_runtime(
        mode="replay",
        runtime_name="replay",
        recordings=RECORDINGS,
        config=_Config(),
    )
    assert runtime.expected_temperature == 0.7
    assert runtime.expected_model == "qwen2.5:7b"


def test_replay_eval_rejects_configured_temperature_mismatch(
    tmp_path: Path, key_provider: object
) -> None:
    """Config 0.7 vs a file recorded at 0.0 must fail the pipeline (CLI exit 1)."""
    path = tmp_path / "named.json"
    runtime = RecordingRuntime(path=path, inner=_NamedInner(), mode="record")
    _complete(runtime, _EXTRACT_PREFIX + "Claims:\n1. Alice\n")
    runtime.save()

    class _Intel:
        llm_model = "qwen2.5:7b"
        temperature = 0.7

    class _Config:
        intelligence = _Intel()

    report = run_eval(
        fixtures_root=FIXTURES,
        names=("day_near_empty",),
        mode="replay",
        runtime_name="replay",
        recordings=path,
        out_dir=tmp_path / "temp-mismatch",
        key=TEST_KEY,
        config=_Config(),
    )
    assert report.pipeline_passed is False
    reason = report.failure_reason or ""
    assert "ReplayParamsError" in reason
    assert "0.7" in reason
    assert (0 if report.pipeline_passed else 1) != 0


def test_legacy_recorded_model_is_unverified_not_config() -> None:
    """Surviving mutant / G1: legacy files must not echo the configured model."""
    runtime = RecordingRuntime(path=RECORDINGS, mode="replay", expected_model="llama3.1:8b")
    assert runtime.recorded_model == "legacy (unverified)"
    assert "llama3.1:8b" not in runtime.recorded_model


def test_legacy_eval_report_model_is_unverified(tmp_path: Path, key_provider: object) -> None:
    class _Intel:
        llm_model = "llama3.1:8b"
        temperature = 0.0

    class _Config:
        intelligence = _Intel()

    report = run_eval(
        fixtures_root=FIXTURES,
        names=("day_near_empty",),
        mode="replay",
        runtime_name="replay",
        recordings=RECORDINGS,
        out_dir=tmp_path / "legacy-model",
        key=TEST_KEY,
        config=_Config(),
    )
    assert report.model == "legacy (unverified)"
    markdown = (tmp_path / "legacy-model" / "report.md").read_text(encoding="utf-8")
    payload = json.loads((tmp_path / "legacy-model" / "report.json").read_text(encoding="utf-8"))
    assert "legacy (unverified)" in markdown
    assert "llama3.1:8b" not in markdown
    assert payload["model"] == "legacy (unverified)"


def test_restore_does_not_rewrite_rank_string_ordinal(tmp_path: Path) -> None:
    """A whole-string ordinal must not rewrite non-id fields such as rank."""
    send = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    prompt = f"Write the daily narrative\nClaims:\n1. [c:{send}] (commitment/observed) Send it\n"
    digest = prompt_hash(
        [{"role": "user", "content": prompt}],
        schema={},
        model="qwen2.5:7b",
        temperature=0.0,
        run_ids=[send],
    )
    path = _write_v1(
        tmp_path / "rank.json",
        [
            {
                "match": digest,
                "model": "qwen2.5:7b",
                "temperature": 0.0,
                "response": {
                    "paragraphs": [{"text": "Sent it [c:1].", "rank": "1", "claim_ids": ["1"]}]
                },
            }
        ],
    )
    token = bind_issued([send], [])
    try:
        replay = RecordingRuntime(path=path, mode="replay")
        result = replay.complete([{"role": "user", "content": prompt}], {})
    finally:
        reset_issued(token)
    paragraph = result["paragraphs"][0]
    assert paragraph["rank"] == "1"
    assert send in str(paragraph["text"])
    assert paragraph["claim_ids"] == [send]


def test_cite_ordinals_ignore_preamble_run_id(tmp_path: Path) -> None:
    """Index-form [c:N] follows claim cites, not a run-issued ID before them."""
    preamble = "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"
    send = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    edit = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
    prompt = (
        f"Context id {preamble}\nWrite the daily narrative\nClaims:\n"
        f"1. [c:{send}] (commitment/observed) Send it\n"
        f"2. [c:{edit}] (activity/observed) Edit doc\n"
    )
    digest = prompt_hash(
        [{"role": "user", "content": prompt}],
        schema={},
        model="qwen2.5:7b",
        temperature=0.0,
        run_ids=[preamble, send, edit],
    )
    path = _write_v1(
        tmp_path / "preamble.json",
        [
            {
                "match": digest,
                "model": "qwen2.5:7b",
                "temperature": 0.0,
                "response": {"paragraphs": [{"text": "Edited the doc [c:2].", "claim_ids": ["2"]}]},
            }
        ],
    )
    token = bind_issued([preamble, send, edit], [])
    try:
        replay = RecordingRuntime(path=path, mode="replay")
        result = replay.complete([{"role": "user", "content": prompt}], {})
    finally:
        reset_issued(token)
    text = str(result["paragraphs"][0]["text"])
    assert edit in text
    assert send not in text
    assert preamble not in text
    assert result["paragraphs"][0]["claim_ids"] == [edit]
