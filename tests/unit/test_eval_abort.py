"""Eval always writes report.json/md, including on abort or hold."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from tests.replay_support import TEST_KEY
from watson.intelligence.eval.recordings import RecordingRuntime
from watson.intelligence.eval.run import DayEval, run_eval
from watson.intelligence.runtime.types import OutputParseError, RuntimeUnavailable
from watson.memory.db import connect
from watson.pipeline.consolidation import ConsolidationWait
from watson.pipeline.jobs import JobState
from watson.pipeline.redaction import redact_ocr_text

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


class _StubRecordInner:
    name = "ollama"

    def complete(
        self, messages: object, schema: object, *, num_predict: int | None = None
    ) -> dict[str, object]:
        del messages, schema, num_predict
        return {"ok": True}

    def embed(self, texts: object) -> list[list[float]]:
        return [[0.0] for _ in list(texts)]


def test_eval_saves_recordings_on_abort(
    tmp_path: Path, key_provider: object, monkeypatch: object
) -> None:
    """R7: recordings not saved on abort."""
    from watson.intelligence.eval import run as run_mod

    recordings = tmp_path / "recorded" / "recordings.json"

    def fake_build(**_kwargs: object) -> RecordingRuntime:
        return RecordingRuntime(path=recordings, inner=_StubRecordInner(), mode="record")

    def fake_one(fixture: Path, **kwargs: object) -> DayEval:
        del fixture
        runtime = kwargs["runtime"]
        complete = getattr(runtime, "complete")
        complete([{"role": "user", "content": "Window: R7 abort sentinel"}], {})
        raise RuntimeError("eval abort after record")

    monkeypatch.setattr(run_mod, "build_eval_runtime", fake_build)
    monkeypatch.setattr(run_mod, "_eval_one", fake_one)
    out = tmp_path / "abort-r7"
    report = run_eval(
        fixtures_root=FIXTURES,
        names=("day_near_empty",),
        mode="record",
        runtime_name="ollama",
        recordings=recordings,
        out_dir=out,
        key=TEST_KEY,
    )
    assert report.failure_reason == "RuntimeError"
    assert recordings.is_file()
    payload = json.loads(recordings.read_text(encoding="utf-8"))
    assert payload["complete"]
    assert payload["complete"][0]["match"] == "R7 abort sentinel"


P1_SENTINEL = "plain pocket calendar marker for report privacy"


def test_eval_report_files_do_not_contain_ocr_sentinel(
    tmp_path: Path, key_provider: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """P1: low-entropy OCR reaches the model prompt, then stays out of reports."""
    assert redact_ocr_text(P1_SENTINEL) == P1_SENTINEL
    src = FIXTURES / "day_near_empty"
    fixtures_root = tmp_path / "fixtures"
    dest = fixtures_root / "day_near_empty"
    shutil.copytree(src, dest)
    injected = False
    for sidecar in dest.rglob("*.json"):
        if sidecar.name == "manifest.json":
            continue
        data = json.loads(sidecar.read_text(encoding="utf-8"))
        lines = (data.get("ocr") or {}).get("lines") or []
        if not lines:
            continue
        lines[0]["text"] = f"{lines[0].get('text', '')} {P1_SENTINEL}"
        sidecar.write_text(json.dumps(data) + "\n", encoding="utf-8")
        injected = True
        break
    assert injected
    prompts: list[str] = []
    original = RecordingRuntime.complete

    def spy_complete(
        self: RecordingRuntime,
        messages: object,
        schema: object,
        *,
        num_predict: int | None = None,
    ) -> dict[str, object]:
        blob = "\n".join(item["content"] for item in messages)  # type: ignore[index]
        prompts.append(blob)
        return original(self, messages, schema, num_predict=num_predict)

    monkeypatch.setattr(RecordingRuntime, "complete", spy_complete)
    out = tmp_path / "eval"
    run_eval(
        fixtures_root=fixtures_root,
        names=("day_near_empty",),
        mode="replay",
        runtime_name="replay",
        recordings=RECORDINGS,
        out_dir=out,
        key=TEST_KEY,
    )
    assert prompts
    assert any(P1_SENTINEL in blob for blob in prompts)
    assert (out / "report.json").is_file()
    payload = json.loads((out / "report.json").read_text(encoding="utf-8"))
    allowed = {
        "mode",
        "runtime",
        "passed",
        "pipeline_passed",
        "aggregate",
        "gate",
        "wall_seconds",
        "peak_rss_bytes",
        "worker_plus_model_peak_bytes",
        "worker_peak_bytes",
        "failure_reason",
        "latency",
        "days",
        "profile",
        "profile_gate",
        "headroom",
        "completed_without_kill",
    }
    assert set(payload) <= allowed
    needle = P1_SENTINEL.encode("utf-8")
    for path in out.rglob("*"):
        if not path.is_file():
            continue
        assert needle not in path.read_bytes(), path


def test_run_eval_passes_measured_consolidate_s_into_profile_gate(
    tmp_path: Path, key_provider: object, monkeypatch: object
) -> None:
    """C4: hardcoding consolidate_s=0.0 or dropping it must fail this test."""
    from watson.intelligence.eval import run as run_mod

    captured: dict[str, object] = {}
    real_gate = run_mod.gate_profile

    def spy_gate(**kwargs: object) -> object:
        captured.update(kwargs)
        return real_gate(**kwargs)

    real_record = run_mod.record_consolidate

    def fake_record(_latency_s: float) -> None:
        real_record(77.7)

    monkeypatch.setattr(run_mod, "gate_profile", spy_gate)
    monkeypatch.setattr(run_mod, "record_consolidate", fake_record)
    report = run_eval(
        fixtures_root=FIXTURES,
        names=("day_near_empty",),
        mode="replay",
        runtime_name="replay",
        recordings=RECORDINGS,
        out_dir=tmp_path / "c4",
        key=TEST_KEY,
        profile="min-spec",
    )
    measured = report.latency["consolidate"]["max_s"]
    assert report.latency["consolidate"]["n"] >= 1
    assert measured == 77.7
    assert captured.get("consolidate_s") == 77.7
    assert captured["consolidate_s"] is not None
    assert "consolidate_s" not in (report.profile_gate.get("failures") or [])


def test_run_eval_min_spec_passes_when_measured_consolidate_exceeds_15_min(
    tmp_path: Path, key_provider: object, monkeypatch: object
) -> None:
    """Min-spec records consolidate_s; a 901 s sample must not fail the profile gate."""
    from watson.intelligence.eval import run as run_mod

    captured: dict[str, object] = {}
    real_gate = run_mod.gate_profile

    def spy_gate(**kwargs: object) -> object:
        captured.update(kwargs)
        return real_gate(**kwargs)

    real_record = run_mod.record_consolidate

    def fake_record(_latency_s: float) -> None:
        real_record(901.0)

    monkeypatch.setattr(run_mod, "gate_profile", spy_gate)
    monkeypatch.setattr(run_mod, "record_consolidate", fake_record)
    report = run_eval(
        fixtures_root=FIXTURES,
        names=("day_near_empty",),
        mode="replay",
        runtime_name="replay",
        recordings=RECORDINGS,
        out_dir=tmp_path / "c4-over",
        key=TEST_KEY,
        profile="min-spec",
    )
    assert captured.get("consolidate_s") == 901.0
    assert report.completed_without_kill == 1.0
    assert report.profile_gate["passed"] is True
    assert "consolidate_s" not in (report.profile_gate.get("failures") or [])


def test_run_eval_min_spec_gate_fails_when_runtime_aborts(
    tmp_path: Path, key_provider: object, monkeypatch: object
) -> None:
    """K6: a killed/unavailable runtime must not hardcode completed_without_kill=1.0."""
    from watson.intelligence.eval import run as run_mod

    captured: dict[str, object] = {}
    real_gate = run_mod.gate_profile

    def spy_gate(**kwargs: object) -> object:
        captured.update(kwargs)
        return real_gate(**kwargs)

    def boom(**_kwargs: object) -> object:
        raise RuntimeUnavailable("Ollama runner killed")

    monkeypatch.setattr(run_mod, "gate_profile", spy_gate)
    monkeypatch.setattr(run_mod, "build_eval_runtime", boom)
    report = run_eval(
        fixtures_root=FIXTURES,
        names=("day_acme_q4",),
        mode="record",
        runtime_name="ollama",
        recordings=tmp_path / "k6-abort" / "recordings.json",
        out_dir=tmp_path / "k6-abort",
        key=TEST_KEY,
        profile="min-spec",
    )
    assert report.days == []
    assert report.failure_reason is not None
    assert report.completed_without_kill == 0.0
    assert captured.get("completed_without_kill") == 0.0
    assert report.profile_gate["passed"] is False
    assert "completed_without_kill" in report.profile_gate["failures"]


def test_run_eval_min_spec_gate_fails_when_a_fixture_day_errors(
    tmp_path: Path, key_provider: object, monkeypatch: object
) -> None:
    """K6: incomplete days fail the min-spec completes-without-kill gate."""
    from watson.intelligence.eval import run as run_mod

    captured: dict[str, object] = {}
    real_gate = run_mod.gate_profile

    def spy_gate(**kwargs: object) -> object:
        captured.update(kwargs)
        return real_gate(**kwargs)

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
        raise ConsolidationWait("day did not finish")

    monkeypatch.setattr(run_mod, "gate_profile", spy_gate)
    monkeypatch.setattr(run_mod, "_eval_one", fake_one)
    report = run_eval(
        fixtures_root=FIXTURES,
        names=("day_acme_q4", "day_near_empty"),
        mode="replay",
        runtime_name="replay",
        recordings=RECORDINGS,
        out_dir=tmp_path / "k6-incomplete",
        key=TEST_KEY,
        profile="min-spec",
    )
    assert report.completed_without_kill == 0.0
    assert any(day.error for day in report.days)
    assert captured.get("completed_without_kill") == 0.0
    assert report.profile_gate["passed"] is False
    assert "completed_without_kill" in report.profile_gate["failures"]


class _FailFirstUnderstand:
    """Two parse errors → one terminal understand job; then replay as normal."""

    def __init__(self, inner: RecordingRuntime) -> None:
        self.inner = inner
        self.name = getattr(inner, "name", "replay")
        self._fails_left = 2

    def complete(
        self,
        messages: object,
        schema: object,
        *,
        num_predict: int | None = None,
    ) -> dict[str, object]:
        if self._fails_left > 0:
            self._fails_left -= 1
            raise OutputParseError("truncated JSON")
        return self.inner.complete(messages, schema, num_predict=num_predict)

    def embed(self, texts: object) -> list[list[float]]:
        return self.inner.embed(texts)

    def end_phase(self) -> None:
        end = getattr(self.inner, "end_phase", None)
        if callable(end):
            end()

    def probe(self) -> None:
        probe = getattr(self.inner, "probe", None)
        if callable(probe):
            probe()


def test_eval_terminally_failed_understand_job_is_flagged_not_blocking(
    tmp_path: Path, key_provider: object, monkeypatch: object
) -> None:
    """R1: FAILED understand jobs are skipped and flagged, not treated as unfinished."""
    from watson.intelligence.eval import run as run_mod

    def fake_build(**_kwargs: object) -> _FailFirstUnderstand:
        return _FailFirstUnderstand(RecordingRuntime(path=RECORDINGS, mode="replay"))

    monkeypatch.setattr(run_mod, "build_eval_runtime", fake_build)
    out = tmp_path / "r1"
    report = run_eval(
        fixtures_root=FIXTURES,
        names=("day_near_empty",),
        mode="replay",
        runtime_name="replay",
        recordings=RECORDINGS,
        out_dir=out,
        key=TEST_KEY,
        profile="min-spec",
    )
    assert report.days
    day = report.days[0]
    assert day.error is None
    assert day.skipped_understand >= 1
    assert day.summary_present is True
    payload = json.loads((out / "report.json").read_text(encoding="utf-8"))
    assert payload["days"][0]["skipped_understand"] >= 1
    assert payload["days"][0]["error"] is None
    conn = connect(out / "day_near_empty" / "watson.db", TEST_KEY, load_vec=False)
    try:
        failed = conn.execute(
            "SELECT COUNT(*) FROM jobs WHERE kind = 'understand' AND state = ?",
            (JobState.FAILED.value,),
        ).fetchone()
        assert int(failed[0]) >= 1
        row = conn.execute("SELECT state, consolidation_gap FROM working_days").fetchone()
        assert row["state"] == "consolidated"
        assert "understand_failed=" in str(row["consolidation_gap"] or "")
    finally:
        conn.close()
