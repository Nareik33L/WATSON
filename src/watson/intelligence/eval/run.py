"""Run understanding → consolidation → daily summary over fixture days."""

from __future__ import annotations

import json
import resource
import sys
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlcipher3 import dbapi2 as sqlcipher

from watson.intelligence.eval.recordings import DEFAULT_RECORDINGS, RecordingRuntime
from watson.intelligence.eval.score import collect_predicted, load_truth, score_day
from watson.intelligence.eval.thresholds import gate_metrics, load_thresholds
from watson.intelligence.runtime import FakeRuntime, build_runtime
from watson.intelligence.runtime.types import RuntimeUnavailable
from watson.memory.db import connect
from watson.memory.migrate import upgrade_head
from watson.pipeline.consolidation import (
    ConsolidationWait,
    consolidate_working_day,
    derived_counts,
)
from watson.pipeline.replay import replay_fixture

V1_DAY_FIXTURES = (
    "day_acme_q4",
    "day_fragmented_multi",
    "day_meeting_heavy",
    "day_sensitive_traps",
    "day_near_empty",
)


@dataclass
class DayEval:
    name: str
    date: str
    metrics: dict[str, float]
    derived_first: dict[str, int]
    derived_second: dict[str, int]
    summary_present: bool
    identical_counts: bool
    narrative_nonempty: bool = False
    citation_count: int = 0
    claims: int = 0

    @property
    def narrative_ok(self) -> bool:
        """Days with claims need a non-empty cited narrative."""
        if self.claims <= 0:
            return self.summary_present
        return self.summary_present and self.narrative_nonempty and self.citation_count > 0


@dataclass
class EvalReport:
    mode: str
    runtime: str
    days: list[DayEval] = field(default_factory=list)
    aggregate: dict[str, float] = field(default_factory=dict)
    gate: dict[str, Any] = field(default_factory=dict)
    wall_seconds: float = 0.0
    peak_rss_bytes: int = 0

    @property
    def pipeline_passed(self) -> bool:
        """Structural / acceptance gate used by replay CI (not a quality claim)."""
        if not self.days or not self.aggregate:
            return False
        if self.gate.get("hard_failures"):
            return False
        failures = list(self.gate.get("failures") or [])
        if "empty_aggregate" in failures:
            return False
        missing = [name for name in load_thresholds() if name not in self.aggregate]
        if missing:
            return False
        return all(day.identical_counts and day.narrative_ok for day in self.days)

    @property
    def passed(self) -> bool:
        """Full §11.2 gate. False when days/aggregate/metrics are missing."""
        if not self.days or not self.aggregate:
            return False
        if not self.gate.get("passed"):
            return False
        return self.pipeline_passed


def build_eval_runtime(
    *,
    mode: str,
    runtime_name: str,
    recordings: Path,
    config: Any | None = None,
) -> Any:
    """Construct the eval runtime. FakeRuntime is explicit ``fake`` only."""
    if runtime_name == "fake":
        return FakeRuntime(recordings=recordings if recordings.is_file() else None)
    if mode == "replay":
        if runtime_name not in {"replay", "recordings"}:
            raise RuntimeUnavailable(
                f"replay mode refuses implicit {runtime_name!r}. "
                "Use --mode replay (default) or --runtime fake."
            )
        return RecordingRuntime(path=recordings, mode="replay")
    if runtime_name != "ollama":
        raise RuntimeUnavailable(
            "record mode requires --runtime ollama (FakeRuntime is --runtime fake)."
        )
    if config is None:
        raise RuntimeUnavailable("record mode needs WatsonConfig to build Ollama")
    bundle = build_runtime(config)
    bundle.probe()
    return RecordingRuntime(path=recordings, inner=bundle.language, mode="record")


def run_eval(
    *,
    fixtures_root: Path,
    names: tuple[str, ...] = V1_DAY_FIXTURES,
    mode: str = "replay",
    runtime_name: str = "replay",
    recordings: Path | None = None,
    out_dir: Path,
    key: bytes,
    config: Any | None = None,
) -> EvalReport:
    started = time.perf_counter()
    path = recordings if recordings is not None else DEFAULT_RECORDINGS
    runtime = build_eval_runtime(
        mode=mode, runtime_name=runtime_name, recordings=path, config=config
    )
    days: list[DayEval] = []
    for name in names:
        fixture = fixtures_root / name
        days.append(_eval_one(fixture, runtime=runtime, out_dir=out_dir / name, key=key))
    if isinstance(runtime, RecordingRuntime):
        runtime.save()
    aggregate = _aggregate([day.metrics for day in days])
    report = EvalReport(
        mode=mode,
        runtime=getattr(runtime, "name", runtime_name),
        days=days,
        aggregate=aggregate,
        gate=gate_metrics(aggregate, load_thresholds()),
        wall_seconds=time.perf_counter() - started,
        peak_rss_bytes=_peak_rss_bytes(),
    )
    _write_reports(out_dir, report)
    return report


def render_markdown(report: EvalReport) -> str:
    lines = [
        "# WATSON M3 eval (§11.2)",
        "",
        f"mode: `{report.mode}`  ",
        f"runtime: `{report.runtime}`  ",
        f"wall_seconds: {report.wall_seconds:.3f}  ",
        f"peak_rss_bytes: {report.peak_rss_bytes}  ",
        f"passed: **{report.passed}**  ",
        f"pipeline_passed: **{report.pipeline_passed}**  ",
        "replay is a pipeline-only gate; quality F1 is measured, not a model claim.",
        "",
        "| Metric | Value |",
        "|---|---:|",
    ]
    for key, value in report.aggregate.items():
        lines.append(f"| {key} | {value:.4f} |")
    lines.extend(["", "## Days", ""])
    for day in report.days:
        mark = "yes" if day.narrative_ok and day.identical_counts else "no"
        lines.append(
            f"- `{day.name}` ({day.date}): summary={day.summary_present} "
            f"narrative_nonempty={day.narrative_nonempty} "
            f"citations={day.citation_count} "
            f"identical_counts={day.identical_counts} ok={mark}"
        )
    if report.gate.get("failures"):
        lines.extend(["", f"failures: {', '.join(report.gate['failures'])}"])
    lines.append("")
    return "\n".join(lines)


def _eval_one(fixture: Path, *, runtime: Any, out_dir: Path, key: bytes) -> DayEval:
    out_dir.mkdir(parents=True, exist_ok=True)
    db_path = out_dir / "watson.db"
    # A leftover DB from a prior run would keep old observations (understand
    # is idempotent per segment) and poison the metric table.
    if db_path.is_file():
        db_path.unlink()
    upgrade_head(db_path, key)
    db_path.chmod(0o600)
    conn = connect(db_path, key, load_vec=False)
    try:
        result = replay_fixture(fixture, conn, instant=True, spool_dir=out_dir / "spool")
        day_id = _day_id(conn, result.date)
        now = _eval_now(result.date)
        # consolidate_working_day already understands, resolves, consolidates,
        # and writes the daily summary. Run it twice for the §12 identity check.
        # One retry: the last fixture segment can close during the first pass
        # and leave a single understand job pending (ConsolidationWait).
        _consolidate_once(conn, day_id, runtime=runtime, now=now, spool_dir=out_dir / "spool")
        first = derived_counts(conn, day_id)
        _consolidate_once(conn, day_id, runtime=runtime, now=now, spool_dir=out_dir / "spool")
        second = derived_counts(conn, day_id)
        truth = load_truth(fixture / "truth.yaml")
        metrics = score_day(conn, day_id, truth)
        predicted = collect_predicted(conn, day_id)
        return DayEval(
            name=fixture.name,
            date=result.date,
            metrics=metrics,
            derived_first=first,
            derived_second=second,
            summary_present=bool(predicted["summary_present"]),
            identical_counts=first == second,
            narrative_nonempty=bool(predicted["narrative_nonempty"]),
            citation_count=int(predicted["citation_count"]),
            claims=int(predicted["claims"]),
        )
    finally:
        conn.close()


def _consolidate_once(
    conn: sqlcipher.Connection,
    day_id: str,
    *,
    runtime: Any,
    now: datetime,
    spool_dir: Path,
) -> None:
    try:
        consolidate_working_day(
            conn, day_id, runtime=runtime, embedding=runtime, now=now, spool_dir=spool_dir
        )
    except ConsolidationWait:
        consolidate_working_day(
            conn, day_id, runtime=runtime, embedding=runtime, now=now, spool_dir=spool_dir
        )


def _day_id(conn: sqlcipher.Connection, date: str) -> str:
    row = conn.execute("SELECT id FROM working_days WHERE date = ?", (date,)).fetchone()
    if row is None:
        raise RuntimeError(f"working day for {date} is missing after replay")
    return str(row[0])


def _eval_now(date: str) -> datetime:
    """A timestamp that can claim jobs enqueued at wall-clock *and* close the day.

    ``close_open_segments`` enqueues understand jobs with ``run_after`` at
    wall clock. Passing only the fixture day's 23:00 would miss those jobs
    when the fixture date is in the past.
    """
    year, month, day = (int(part) for part in date.split("-"))
    end = datetime(year, month, day, 23, 0, tzinfo=UTC)
    wall = datetime.now(UTC)
    return wall if wall > end else end


def _aggregate(rows: list[dict[str, float]]) -> dict[str, float]:
    if not rows:
        return {}
    keys = rows[0].keys()
    out: dict[str, float] = {}
    for key in keys:
        values = [float(row[key]) for row in rows]
        if key == "unsupported_achievements":
            out[key] = float(sum(values))
        else:
            out[key] = sum(values) / len(values)
    return out


def _peak_rss_bytes() -> int:
    rss = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    if sys.platform == "darwin":
        return rss
    return rss * 1024


def _write_reports(out_dir: Path, report: EvalReport) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "mode": report.mode,
        "runtime": report.runtime,
        "passed": report.passed,
        "pipeline_passed": report.pipeline_passed,
        "aggregate": report.aggregate,
        "gate": report.gate,
        "wall_seconds": report.wall_seconds,
        "peak_rss_bytes": report.peak_rss_bytes,
        "days": [
            {
                "name": day.name,
                "date": day.date,
                "metrics": day.metrics,
                "derived_first": day.derived_first,
                "derived_second": day.derived_second,
                "summary_present": day.summary_present,
                "identical_counts": day.identical_counts,
                "narrative_nonempty": day.narrative_nonempty,
                "citation_count": day.citation_count,
                "claims": day.claims,
            }
            for day in report.days
        ],
    }
    (out_dir / "report.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    (out_dir / "report.md").write_text(render_markdown(report), encoding="utf-8")
