"""RecordingRuntime: replay committed JSON only, no truth lexicon."""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any

from tests.replay_support import TEST_KEY
from watson.intelligence.eval.recordings import RecordingRuntime
from watson.intelligence.eval.run import run_eval
from watson.intelligence.runtime.types import RuntimeUnavailable

REPO = Path(__file__).resolve().parents[2]
FIXTURES = REPO / "tests" / "fixtures"
RECORDINGS = FIXTURES / "eval" / "recordings.json"
QWEN_RECORDINGS = Path(
    "/home/ubuntu/.cursor/projects/agent-repos-WATSON/uploads/recordings_c40e.json"
)

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


def _sha(blob: str) -> str:
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


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


def test_record_stores_full_prompt_hash(tmp_path: Path) -> None:
    """Surviving mutant: _match_key still stores Window / first 80 chars."""
    path = tmp_path / "rec.json"
    inner = _StubInner({"decisions": [], "commitments": [{"text": "A"}]})
    runtime = RecordingRuntime(path=path, inner=inner, mode="record")
    blob = _EXTRACT_PREFIX + "Claims:\n1. [commitment/observed] Send Priya the slide outline\n"
    result = _complete(runtime, blob)
    runtime.save()
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert result["commitments"][0]["text"] == "A"
    assert payload["complete"][0]["match"] == _sha(blob)
    assert payload["complete"][0]["match"] != blob[:80]


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


def test_legacy_shared_prefix_does_not_reuse_another_day(tmp_path: Path) -> None:
    """Legacy 80-char extract keys: consume each row once, never silent reuse."""
    prefix = _EXTRACT_PREFIX[:80]
    day_a = _EXTRACT_PREFIX + "Claims:\n1. [commitment/observed] Send Priya the slide outline\n"
    day_b = _EXTRACT_PREFIX + "Claims:\n(none)\n"
    path = _write_recordings(
        tmp_path / "legacy.json",
        [
            {
                "match": prefix,
                "response": {"commitments": [{"text": "Commit to complete project X by Q4"}]},
            },
            {"match": prefix, "response": {"commitments": []}},
        ],
    )
    runtime = RecordingRuntime(path=path, mode="replay")
    first = _complete(runtime, day_a)
    second = _complete(runtime, day_b)
    assert first["commitments"][0]["text"] == "Commit to complete project X by Q4"
    assert second["commitments"] == []
    assert second != first


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
    if QWEN_RECORDINGS.is_file():
        qwen = RecordingRuntime(path=QWEN_RECORDINGS, mode="replay")
        assert qwen.complete_calls == 0
        _complete(qwen, "Window: cedar daily — harbor ferry\n1. civic news")


def test_legacy_fallback_emits_a_warning(tmp_path: Path, caplog: Any) -> None:
    path = _write_recordings(
        tmp_path / "warn.json",
        [{"match": "Extract cross-segment", "response": {"commitments": []}}],
    )
    runtime = RecordingRuntime(path=path, mode="replay")
    with caplog.at_level(logging.WARNING):
        _complete(runtime, "Extract cross-segment decisions\nClaims:\n1. Alice\n")
    assert any("legacy" in rec.getMessage().casefold() for rec in caplog.records)


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
