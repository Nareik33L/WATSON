"""Eval always writes report.json/md, including on abort or hold."""

from __future__ import annotations

import json
from pathlib import Path

from tests.replay_support import TEST_KEY
from watson.intelligence.eval.run import DayEval, run_eval
from watson.intelligence.runtime.types import RuntimeUnavailable
from watson.pipeline.consolidation import ConsolidationWait

REPO = Path(__file__).resolve().parents[2]
FIXTURES = REPO / "tests" / "fixtures"
RECORDINGS = FIXTURES / "eval" / "recordings.json"


def test_eval_writes_report_on_hold(
    tmp_path: Path, key_provider: object, monkeypatch: object
) -> None:
    from watson.intelligence.eval import run as run_mod

    def fake_one(fixture: Path, **_kwargs: object) -> DayEval:
        if fixture.name == "day_acme_q4":
            return DayEval(
                name="day_acme_q4",
                date="2026-10-31",
                metrics={"project_f1": 0.5},
                derived_first={"summaries": 1},
                derived_second={"summaries": 1},
                summary_present=True,
                identical_counts=True,
                narrative_nonempty=True,
                citation_count=1,
                claims=1,
            )
        raise ConsolidationWait("understanding held")

    monkeypatch.setattr(run_mod, "_eval_one", fake_one)
    out = tmp_path / "abort"
    report = run_eval(
        fixtures_root=FIXTURES,
        names=("day_acme_q4", "day_near_empty"),
        mode="replay",
        runtime_name="replay",
        recordings=RECORDINGS,
        out_dir=out,
        key=TEST_KEY,
    )
    assert (out / "report.json").is_file()
    assert (out / "report.md").is_file()
    payload = json.loads((out / "report.json").read_text(encoding="utf-8"))
    assert payload["failure_reason"] and "ConsolidationWait" in payload["failure_reason"]
    assert payload["days"][0]["name"] == "day_acme_q4"
    assert payload["days"][0]["error"] is None
    assert payload["days"][1]["error"]
    assert report.pipeline_passed is False
    assert "failure_reason" in (out / "report.md").read_text(encoding="utf-8")
    assert "latency" in payload
    assert "timeouts" in payload["latency"]


def test_eval_writes_report_when_runtime_is_unavailable(
    tmp_path: Path, key_provider: object, monkeypatch: object
) -> None:
    from watson.intelligence.eval import run as run_mod

    def boom(**_kwargs: object) -> object:
        raise RuntimeUnavailable("Ollama is not reachable at http://127.0.0.1:11434")

    monkeypatch.setattr(run_mod, "build_eval_runtime", boom)
    out = tmp_path / "hold"
    report = run_eval(
        fixtures_root=FIXTURES,
        names=("day_acme_q4",),
        mode="record",
        runtime_name="ollama",
        recordings=out / "recordings.json",
        out_dir=out,
        key=TEST_KEY,
    )
    assert (out / "report.json").is_file()
    assert (out / "report.md").is_file()
    assert report.days == []
    assert report.failure_reason is not None
    assert "RuntimeUnavailable" in report.failure_reason
    assert report.pipeline_passed is False
