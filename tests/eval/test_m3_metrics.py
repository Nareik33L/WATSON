"""M3 acceptance: §11.2 replay gate, identical consolidation, daily summary."""

from __future__ import annotations

from pathlib import Path

from tests.replay_support import TEST_KEY, V1_DAY_FIXTURES
from watson.intelligence.eval.run import run_eval
from watson.intelligence.eval.thresholds import HARD_HUNDRED, HARD_ZERO

REPO = Path(__file__).resolve().parents[2]
FIXTURES = REPO / "tests" / "fixtures"
RECORDINGS = FIXTURES / "eval" / "recordings.json"


def test_m3_replay_meets_section_11_2_and_acceptance(
    tmp_path: Path, key_provider: object
) -> None:
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
    assert report.gate["passed"], f"§11.2 failures: {report.gate['failures']} {report.aggregate}"
    assert not report.gate["hard_failures"]
    for name in HARD_ZERO:
        assert report.aggregate[name] == 0.0
    for name in HARD_HUNDRED:
        assert report.aggregate[name] == 1.0
    assert report.passed


def test_eval_refuses_implicit_fake_in_replay() -> None:
    from watson.intelligence.eval.run import build_eval_runtime
    from watson.intelligence.runtime.types import RuntimeUnavailable

    try:
        build_eval_runtime(
            mode="replay", runtime_name="ollama", recordings=RECORDINGS, config=None
        )
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
        build_eval_runtime(
            mode="record", runtime_name="replay", recordings=RECORDINGS, config=None
        )
    except RuntimeUnavailable as exc:
        assert "ollama" in str(exc).lower()
    else:
        raise AssertionError("record mode must not default to FakeRuntime")
