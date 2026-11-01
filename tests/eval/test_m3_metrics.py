"""M3 acceptance: §11.2 replay gate, identical consolidation, daily summary."""

from __future__ import annotations

from pathlib import Path

from tests.replay_support import TEST_KEY, V1_DAY_FIXTURES
from watson.intelligence.eval.run import run_eval
from watson.intelligence.eval.thresholds import HARD_HUNDRED, HARD_ZERO

REPO = Path(__file__).resolve().parents[2]
FIXTURES = REPO / "tests" / "fixtures"
RECORDINGS = FIXTURES / "eval" / "recordings.json"


def test_m3_replay_meets_section_11_2_and_acceptance(tmp_path: Path, key_provider: object) -> None:
    report = run_eval(
        fixtures_root=FIXTURES,
        names=V1_DAY_FIXTURES,
        mode="replay",
        runtime_name="replay",
        recordings=RECORDINGS,
        out_dir=tmp_path / "eval",
        key=TEST_KEY,
    )
    assert (tmp_path / "eval" / "report.json").is_file()
    assert (tmp_path / "eval" / "report.md").is_file()
    assert report.runtime == "replay"
    assert report.days, "eval produced no fixture days"
    assert {day.name for day in report.days} == set(V1_DAY_FIXTURES)
    for day in report.days:
        assert day.summary_present, f"{day.name}: missing daily summary"
        assert day.identical_counts, f"{day.name}: consolidation counts drifted"
        assert day.derived_first.get("summaries", 0) >= 1
        if day.claims > 0:
            assert day.narrative_nonempty, f"{day.name}: empty narrative"
            assert day.citation_count > 0, f"{day.name}: citations_resolvable is vacuous"
            assert day.metrics["citations_resolvable"] == 1.0
    assert report.pipeline_passed, f"pipeline: {report.gate} {report.aggregate}"
    assert not report.gate["hard_failures"], report.gate["hard_failures"]
    for name in HARD_ZERO:
        assert report.aggregate[name] == 0.0
    for name in HARD_HUNDRED:
        assert report.aggregate[name] == 1.0
    # Soft §11.2 numbers are measured, not forced to 1.0. Do not lower thresholds.
    # Repeated same-window drafts share a description, so the duplicate rate
    # is honestly > 0. A hardcoded 0.0 must fail this assertion.
    assert report.aggregate["duplicate_observation_rate"] > 0.0
    assert report.aggregate["project_f1"] < 1.0 or report.aggregate["people_f1"] < 1.0


def test_zero_fixtures_fail_the_gate(tmp_path: Path, key_provider: object) -> None:
    report = run_eval(
        fixtures_root=FIXTURES,
        names=(),
        mode="replay",
        runtime_name="replay",
        recordings=RECORDINGS,
        out_dir=tmp_path / "empty",
        key=TEST_KEY,
    )
    assert report.days == []
    assert report.aggregate == {}
    assert report.gate["passed"] is False
    assert report.passed is False
    assert report.pipeline_passed is False


def test_bad_replay_recordings_fail_hard_thresholds(tmp_path: Path, key_provider: object) -> None:
    import json

    bad = tmp_path / "bad.json"
    bad.write_text(
        json.dumps(
            {
                "source": "deliberately-bad",
                "complete": [
                    {
                        "match": "Write the daily narrative",
                        "response": {
                            "paragraphs": [{"text": "Uncited ghost work.", "claim_ids": []}]
                        },
                    },
                    {
                        "match": "Cluster these observations",
                        "response": {"activities": []},
                    },
                    {
                        "match": "Extract cross-segment",
                        "response": {
                            "entity_deltas": [],
                            "decisions": [],
                            "commitments": [],
                        },
                    },
                    {
                        "match": "inbox",
                        "response": {
                            "activity_type": "knowledge_work",
                            "description": "Invented a completion.",
                            "confidence": 0.9,
                            "claims": [
                                {
                                    "kind": "achievement",
                                    "text": "Ghost shipped the entire product",
                                    "assertion": "observed",
                                    "evidence": [
                                        {
                                            "snippet": "inbox",
                                            "line_start": 1,
                                            "line_end": 1,
                                        }
                                    ],
                                }
                            ],
                            "project_candidates": ["Ghost Project"],
                            "people_candidates": [],
                        },
                    },
                ],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    report = run_eval(
        fixtures_root=FIXTURES,
        names=("day_near_empty",),
        mode="replay",
        runtime_name="replay",
        recordings=bad,
        out_dir=tmp_path / "bad-eval",
        key=TEST_KEY,
    )
    assert report.aggregate["unsupported_achievements"] >= 1.0
    assert report.aggregate["citations_resolvable"] < 1.0 or report.days[0].citation_count == 0
    assert report.gate["passed"] is False
    assert report.passed is False
    assert "unsupported_achievements" in report.gate["failures"] or (
        "citations_resolvable" in report.gate["failures"]
    )


def test_eval_refuses_implicit_fake_in_replay() -> None:
    from watson.intelligence.eval.run import build_eval_runtime
    from watson.intelligence.runtime.types import RuntimeUnavailable

    try:
        build_eval_runtime(mode="replay", runtime_name="ollama", recordings=RECORDINGS, config=None)
    except RuntimeUnavailable as exc:
        assert "fake" in str(exc).lower() or "refuses" in str(exc).lower()
    else:
        raise AssertionError("replay+ollama must not silently become FakeRuntime")


def test_eval_fake_runtime_is_explicit_opt_in() -> None:
    from watson.intelligence.eval.run import build_eval_runtime
    from watson.intelligence.runtime import FakeRuntime

    runtime = build_eval_runtime(
        mode="replay", runtime_name="fake", recordings=RECORDINGS, config=None
    )
    assert isinstance(runtime, FakeRuntime)
    assert runtime.name == "fake"


def test_eval_record_mode_requires_ollama() -> None:
    from watson.intelligence.eval.run import build_eval_runtime
    from watson.intelligence.runtime.types import RuntimeUnavailable

    try:
        build_eval_runtime(mode="record", runtime_name="replay", recordings=RECORDINGS, config=None)
    except RuntimeUnavailable as exc:
        assert "ollama" in str(exc).lower()
    else:
        raise AssertionError("record mode must not default to FakeRuntime")
