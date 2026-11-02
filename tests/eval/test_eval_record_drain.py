"""Record-mode eval driver drains every understand job on the queue clock."""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from tests.replay_support import TEST_KEY, V1_DAY_FIXTURES
from watson.intelligence.eval.recordings import RecordingRuntime
from watson.intelligence.eval.run import run_eval
from watson.intelligence.runtime import UNDERSTAND_JOB_KIND
from watson.memory.db import connect, iso_now
from watson.pipeline.jobs import JobState
from watson.pipeline.replay import replay_fixture

REPO = Path(__file__).resolve().parents[2]
FIXTURES = REPO / "tests" / "fixtures"
RECORDINGS = FIXTURES / "eval" / "recordings.json"


class _Clock:
    def __init__(self, start: datetime) -> None:
        self._now = start

    def now(self) -> datetime:
        return self._now


class _SlowRuntime:
    """Wall-clock delay per complete/embed; forwards to a replay recording runtime."""

    def __init__(self, inner: RecordingRuntime, *, delay_s: float = 0.02) -> None:
        self._inner = inner
        self.name = "ollama"
        self.delay_s = delay_s
        self.complete_calls = 0

    def complete(
        self, messages: Any, schema: Any, *, num_predict: int | None = None
    ) -> dict[str, Any]:
        time.sleep(self.delay_s)
        self.complete_calls += 1
        return self._inner.complete(messages, schema, num_predict=num_predict)

    def embed(self, texts: Any) -> list[list[float]]:
        time.sleep(self.delay_s)
        return self._inner.embed(texts)

    def end_phase(self) -> None:
        end = getattr(self._inner, "end_phase", None)
        if callable(end):
            end()

    def probe(self) -> None:
        return None


@pytest.mark.timeout(60)
def test_record_driver_drains_late_jobs_over_all_fixture_days(
    tmp_path: Path, key_provider: object, monkeypatch: object
) -> None:
    """Record-mode eval drains on the queue clock (jumps to the latest pending run_after).

    Jobs scheduled after day start still complete; every day consolidates once.
    """
    from watson.intelligence.eval import run as run_mod

    clock = _Clock(datetime(2026, 10, 31, 9, 0, tzinfo=UTC))
    slow = _SlowRuntime(RecordingRuntime(path=RECORDINGS, mode="replay"), delay_s=0.02)
    real_replay = replay_fixture

    def delayed_replay(fixture: Path, conn: object, **kwargs: object) -> object:
        result = real_replay(fixture, conn, **kwargs)
        year, month, day = (int(part) for part in result.date.split("-"))
        later = datetime(year, month, day, 23, 30, tzinfo=UTC)
        conn.execute(  # type: ignore[union-attr]
            "UPDATE jobs SET run_after = ? WHERE kind = ?",
            (iso_now(later), UNDERSTAND_JOB_KIND),
        )
        conn.commit()  # type: ignore[union-attr]
        return result

    def fake_build(**_kwargs: object) -> _SlowRuntime:
        return slow

    monkeypatch.setattr(run_mod, "build_eval_runtime", fake_build)
    monkeypatch.setattr(run_mod, "replay_fixture", delayed_replay)

    started = time.perf_counter()
    out = tmp_path / "record-drain"
    report = run_eval(
        fixtures_root=FIXTURES,
        names=V1_DAY_FIXTURES,
        mode="record",
        runtime_name="ollama",
        recordings=RECORDINGS,
        out_dir=out,
        key=TEST_KEY,
        now_fn=clock.now,
    )
    elapsed = time.perf_counter() - started
    assert elapsed >= 0.02
    assert slow.complete_calls >= 1
    assert report.failure_reason is None
    assert {day.name for day in report.days} == set(V1_DAY_FIXTURES)
    payload = json.loads((out / "report.json").read_text(encoding="utf-8"))
    assert payload["worker_peak_bytes"] is not None
    assert payload["latency"]["consolidate"]["total_s"] >= 0
    assert payload["latency"]["prompt"]["n"] >= 1
    for day in report.days:
        assert day.error is None, day.name
        assert day.identical_counts, day.name
        assert day.derived_first.get("summaries", 0) == 1
        assert day.derived_second.get("summaries", 0) == 1
        db = connect(out / day.name / "watson.db", TEST_KEY, load_vec=False)
        try:
            states = {
                str(row[0])
                for row in db.execute(
                    "SELECT state FROM jobs WHERE kind = ?", (UNDERSTAND_JOB_KIND,)
                )
            }
            assert JobState.PENDING.value not in states
            assert JobState.CLAIMED.value not in states
            assert JobState.FAILED.value not in states
            if states:
                assert states == {JobState.COMPLETED.value}
            day_state = str(
                db.execute("SELECT state FROM working_days WHERE date = ?", (day.date,)).fetchone()[
                    0
                ]
            )
            assert day_state == "consolidated"
            n_audit = int(
                db.execute(
                    "SELECT COUNT(*) FROM audit_events WHERE kind = ?",
                    ("day_consolidated",),
                ).fetchone()[0]
            )
            # Two identity consolidations each record an audit row.
            assert n_audit >= 1
        finally:
            db.close()
        if day.claims > 0:
            assert day.narrative_nonempty, day.name
            assert day.citation_count > 0, day.name
            assert day.metrics["citations_resolvable"] == 1.0
    assert report.pipeline_passed
