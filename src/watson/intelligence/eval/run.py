"""Run understanding → consolidation → daily summary over fixture days."""

from __future__ import annotations

import gc
import json
import resource
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlcipher3 import dbapi2 as sqlcipher

from watson.config.schema import DEFAULT_SEGMENT_CLOSE_AFTER_SECONDS
from watson.intelligence.eval.footprint import FootprintSampler
from watson.intelligence.eval.recordings import DEFAULT_RECORDINGS, RecordingRuntime
from watson.intelligence.eval.score import collect_predicted, load_truth, score_day
from watson.intelligence.eval.thresholds import (
    gate_metrics,
    gate_profile,
    headroom_status,
    load_thresholds,
    select_eval_profile,
)
from watson.intelligence.runtime import UNDERSTAND_JOB_KIND, FakeRuntime, build_runtime
from watson.intelligence.runtime.stats import (
    InferenceStats,
    TimingRuntime,
    bind_stats,
    record_consolidate,
    reset_stats,
)
from watson.intelligence.runtime.types import RuntimeUnavailable
from watson.memory.db import connect, parse_iso
from watson.memory.migrate import upgrade_head
from watson.pipeline.consolidation import (
    ConsolidationError,
    ConsolidationWait,
    consolidate_working_day,
    derived_counts,
    skipped_understand_segment_ids,
    understand_block_reason,
)
from watson.pipeline.frames import PIPELINE_VERSION
from watson.pipeline.jobs import JobState
from watson.pipeline.replay import replay_fixture
from watson.pipeline.segmentation import close_open_segments
from watson.pipeline.understanding import (
    drain_understand_jobs,
    enqueue_eligible_understand_jobs,
)

_MAX_EVAL_DRAIN_ROUNDS = 10_000

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
    consolidate_s: float = 0.0
    skipped_understand: int = 0
    error: str | None = None

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
    worker_plus_model_peak_bytes: int | None = None
    worker_peak_bytes: int | None = None
    failure_reason: str | None = None
    latency: dict[str, Any] = field(default_factory=dict)
    profile: str = "primary"
    profile_gate: dict[str, Any] = field(default_factory=dict)
    headroom: dict[str, Any] = field(default_factory=dict)
    measure_model: bool = False
    completed_without_kill: float = 0.0

    @property
    def pipeline_passed(self) -> bool:
        """Structural / acceptance gate used by replay CI (not a quality claim)."""
        if self.failure_reason:
            return False
        if not self.days or not self.aggregate:
            return False
        if any(day.error for day in self.days):
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
        if self.measure_model and not self.profile_gate.get("passed"):
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
    rss_pids: tuple[int, ...] = (),
    measure_model: bool = False,
    profile: str | None = None,
    now_fn: Callable[[], datetime] | None = None,
) -> EvalReport:
    started = time.perf_counter()
    active_profile = select_eval_profile(profile)
    path = recordings if recordings is not None else DEFAULT_RECORDINGS
    stats = InferenceStats()
    stats_token = bind_stats(stats)
    sampler = FootprintSampler(extra_pids=list(rss_pids), measure_model=measure_model)
    sampler.start()
    days: list[DayEval] = []
    failure_reason: str | None = None
    runtime: Any = None
    try:
        runtime = build_eval_runtime(
            mode=mode, runtime_name=runtime_name, recordings=path, config=config
        )
        runtime = TimingRuntime(runtime, stats)
        for name in names:
            fixture = fixtures_root / name
            try:
                days.append(
                    _eval_one(
                        fixture,
                        runtime=runtime,
                        out_dir=out_dir / name,
                        key=key,
                        now_fn=now_fn or (lambda: datetime.now(UTC)),
                    )
                )
            except Exception as exc:
                failure_reason = _format_failure(exc)
                days.append(_failed_day(name, exc))
            gc.collect()
    except Exception as exc:
        failure_reason = _format_failure(exc)
    finally:
        try:
            inner = getattr(runtime, "_inner", runtime)
            if isinstance(inner, RecordingRuntime):
                inner.save()
            elif isinstance(runtime, RecordingRuntime):
                runtime.save()
        except Exception:
            pass
        worker_plus_model = sampler.stop()
        reset_stats(stats_token)
        scored = [day.metrics for day in days if not day.error and day.metrics]
        aggregate = _aggregate(scored)
        latency = stats.summary()
        understand = latency.get("understand_segment") if isinstance(latency, dict) else None
        understand_p95 = understand.get("p95_s") if isinstance(understand, dict) else None
        consolidate = latency.get("consolidate") if isinstance(latency, dict) else None
        consolidate_max = consolidate.get("max_s") if isinstance(consolidate, dict) else None
        completed_without_kill = _completed_without_kill(names, days, failure_reason=failure_reason)
        report = EvalReport(
            mode=mode,
            runtime=getattr(runtime, "name", runtime_name) if runtime is not None else runtime_name,
            days=days,
            aggregate=aggregate,
            gate=gate_metrics(aggregate, load_thresholds()),
            wall_seconds=time.perf_counter() - started,
            peak_rss_bytes=_peak_rss_bytes(),
            worker_plus_model_peak_bytes=worker_plus_model,
            worker_peak_bytes=sampler.worker_peak_bytes,
            failure_reason=failure_reason,
            latency=latency,
            profile=active_profile,
            completed_without_kill=completed_without_kill,
            profile_gate=gate_profile(
                understand_p95_s=(
                    understand_p95 if isinstance(understand_p95, int | float) else None
                ),
                worker_plus_model_peak_bytes=worker_plus_model,
                consolidate_s=(
                    consolidate_max if isinstance(consolidate_max, int | float) else None
                ),
                completed_without_kill=completed_without_kill,
                profile=active_profile,
            ),
            headroom=headroom_status(
                understand_p95_s=(
                    understand_p95 if isinstance(understand_p95, int | float) else None
                ),
                worker_plus_model_peak_bytes=worker_plus_model,
            ),
            measure_model=measure_model,
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
        f"worker_plus_model_peak_bytes: {report.worker_plus_model_peak_bytes}  ",
        f"worker_peak_bytes: {report.worker_peak_bytes}  ",
        f"profile: `{report.profile}`  ",
        f"completed_without_kill: {report.completed_without_kill}  ",
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
        if day.error:
            lines.append(f"- `{day.name}` ({day.date}): ERROR {day.error}")
            continue
        mark = "yes" if day.narrative_ok and day.identical_counts else "no"
        lines.append(
            f"- `{day.name}` ({day.date}): summary={day.summary_present} "
            f"narrative_nonempty={day.narrative_nonempty} "
            f"citations={day.citation_count} "
            f"consolidate_s={day.consolidate_s:.3f} "
            f"skipped_understand={day.skipped_understand} "
            f"identical_counts={day.identical_counts} ok={mark}"
        )
    if report.failure_reason:
        lines.extend(["", f"failure_reason: {report.failure_reason}"])
    if report.gate.get("failures"):
        lines.extend(["", f"failures: {', '.join(report.gate['failures'])}"])
    if report.measure_model and report.profile_gate.get("failures"):
        names = ", ".join(report.profile_gate["failures"])
        lines.extend(["", f"profile_failures ({report.profile}): {names}"])
    if report.headroom:
        p95 = report.headroom.get("understand_p95_s") or {}
        peak = report.headroom.get("worker_plus_model_peak_bytes") or {}
        lines.extend(
            [
                "",
                "headroom (non-gating 8 GB): "
                f"p95_within={p95.get('within')} peak_within={peak.get('within')}",
            ]
        )
    latency = report.latency
    if latency:
        lines.extend(["", "## Latency", ""])
        complete = latency.get("complete") if isinstance(latency, dict) else None
        understand = latency.get("understand_segment") if isinstance(latency, dict) else None
        consolidate = latency.get("consolidate") if isinstance(latency, dict) else None
        prompt = latency.get("prompt") if isinstance(latency, dict) else None
        timeouts = latency.get("timeouts") if isinstance(latency, dict) else 0
        if isinstance(complete, dict):
            lines.append(
                f"- complete p50={complete.get('p50_s')} p95={complete.get('p95_s')} "
                f"max={complete.get('max_s')} n={complete.get('n')}"
            )
        if isinstance(understand, dict):
            lines.append(
                f"- understand_segment p50={understand.get('p50_s')} "
                f"p95={understand.get('p95_s')} max={understand.get('max_s')} "
                f"n={understand.get('n')}"
            )
            per = understand.get("per_segment_s") or []
            if per:
                lines.append("- per_segment_s: " + ", ".join(f"{item:.3f}" for item in per))
        if isinstance(consolidate, dict):
            lines.append(
                f"- consolidate p50={consolidate.get('p50_s')} "
                f"p95={consolidate.get('p95_s')} max={consolidate.get('max_s')} "
                f"total={consolidate.get('total_s')} n={consolidate.get('n')}"
            )
        if isinstance(prompt, dict):
            lines.append(
                f"- prompt chars p50={prompt.get('chars_p50')} "
                f"p95={prompt.get('chars_p95')} max={prompt.get('chars_max')} "
                f"tokens_est p50={prompt.get('tokens_est_p50')} "
                f"p95={prompt.get('tokens_est_p95')} max={prompt.get('tokens_est_max')} "
                f"n={prompt.get('n')}"
            )
        lines.append(f"- timeouts: {timeouts}")
    lines.append("")
    return "\n".join(lines)


def _eval_one(
    fixture: Path,
    *,
    runtime: Any,
    out_dir: Path,
    key: bytes,
    now_fn: Callable[[], datetime],
) -> DayEval:
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
        setter = getattr(runtime, "set_day", None)
        if callable(setter):
            setter(fixture.name, result.date)
        day_id = _day_id(conn, result.date)
        spool = out_dir / "spool"
        first = _eval_consolidate(
            conn, day_id, runtime=runtime, date=result.date, now_fn=now_fn, spool_dir=spool
        )
        second = _eval_consolidate(
            conn, day_id, runtime=runtime, date=result.date, now_fn=now_fn, spool_dir=spool
        )
        truth = load_truth(fixture / "truth.yaml")
        metrics = score_day(conn, day_id, truth)
        predicted = collect_predicted(conn, day_id)
        skipped = skipped_understand_segment_ids(conn, day_id)
        return DayEval(
            name=fixture.name,
            date=result.date,
            metrics=metrics,
            derived_first=first[0],
            derived_second=second[0],
            summary_present=bool(predicted["summary_present"]),
            identical_counts=first[0] == second[0],
            narrative_nonempty=bool(predicted["narrative_nonempty"]),
            citation_count=int(predicted["citation_count"]),
            claims=int(predicted["claims"]),
            consolidate_s=first[1] + second[1],
            skipped_understand=len(skipped),
        )
    finally:
        conn.close()


def _drain_until_idle(
    conn: sqlcipher.Connection,
    *,
    runtime: Any,
    day_id: str,
    date: str,
    now_fn: Callable[[], datetime],
) -> datetime:
    """Drain until no understand job is claimable, pending, or unresolved.

    Uses the queue's ``run_after`` clock so jobs enqueued after day start,
    or parked with backoff, become claimable. Does not swallow the
    production ConsolidationWait hold — it keeps draining until that hold
    would not fire.
    """
    for _ in range(_MAX_EVAL_DRAIN_ROUNDS):
        now = _eval_queue_now(conn, date=date, now_fn=now_fn)
        close_open_segments(
            conn,
            working_day_id=day_id,
            close_after_seconds=DEFAULT_SEGMENT_CLOSE_AFTER_SECONDS,
            now=now,
        )
        enqueue_eligible_understand_jobs(
            conn,
            working_day_id=day_id,
            close_after_seconds=DEFAULT_SEGMENT_CLOSE_AFTER_SECONDS,
            now=now,
        )
        drain_understand_jobs(conn, runtime=runtime, embedding=runtime, now=now)
        blocked = understand_block_reason(
            conn,
            day_id,
            pipeline_version=PIPELINE_VERSION,
            close_after_seconds=DEFAULT_SEGMENT_CLOSE_AFTER_SECONDS,
            now=now,
        )
        if blocked is None:
            _reconcile_understand_jobs(conn, day_id)
            return now
        if blocked == "error":
            raise ConsolidationError("pending understanding")
    raise ConsolidationWait("understanding held")


def _eval_consolidate(
    conn: sqlcipher.Connection,
    day_id: str,
    *,
    runtime: Any,
    date: str,
    now_fn: Callable[[], datetime],
    spool_dir: Path,
) -> tuple[dict[str, int], float]:
    now = _drain_until_idle(conn, runtime=runtime, day_id=day_id, date=date, now_fn=now_fn)
    started = time.perf_counter()
    try:
        consolidate_working_day(
            conn, day_id, runtime=runtime, embedding=runtime, now=now, spool_dir=spool_dir
        )
    except ConsolidationWait:
        now = _drain_until_idle(conn, runtime=runtime, day_id=day_id, date=date, now_fn=now_fn)
        started = time.perf_counter()
        consolidate_working_day(
            conn, day_id, runtime=runtime, embedding=runtime, now=now, spool_dir=spool_dir
        )
    elapsed = time.perf_counter() - started
    record_consolidate(elapsed)
    return derived_counts(conn, day_id), elapsed


def _reconcile_understand_jobs(conn: sqlcipher.Connection, day_id: str) -> None:
    from watson.pipeline.consolidation import _understand_jobs_for_day

    jobs = _understand_jobs_for_day(conn, day_id, PIPELINE_VERSION)
    unfinished = [job for job in jobs if job.state in {JobState.PENDING, JobState.CLAIMED}]
    if unfinished:
        raise ConsolidationWait("understanding held")


def _eval_queue_now(
    conn: sqlcipher.Connection,
    *,
    date: str,
    now_fn: Callable[[], datetime],
) -> datetime:
    """At least the caller clock, day-end, and any pending ``run_after``."""
    times = [_eval_now(date, now=now_fn())]
    rows = conn.execute(
        "SELECT run_after FROM jobs WHERE kind = ? AND state = ?",
        (UNDERSTAND_JOB_KIND, JobState.PENDING.value),
    ).fetchall()
    for row in rows:
        raw = row[0] if not hasattr(row, "keys") else row["run_after"]
        if raw:
            times.append(parse_iso(str(raw)))
    return max(times)


def _day_id(conn: sqlcipher.Connection, date: str) -> str:
    row = conn.execute("SELECT id FROM working_days WHERE date = ?", (date,)).fetchone()
    if row is None:
        raise RuntimeError(f"working day for {date} is missing after replay")
    return str(row[0])


def _eval_now(date: str, now: datetime | None = None) -> datetime:
    """A timestamp that can claim jobs enqueued at wall-clock *and* close the day."""
    year, month, day = (int(part) for part in date.split("-"))
    end = datetime(year, month, day, 23, 0, tzinfo=UTC)
    wall = now if now is not None else datetime.now(UTC)
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


def _completed_without_kill(
    names: tuple[str, ...],
    days: list[DayEval],
    *,
    failure_reason: str | None,
) -> float:
    """1.0 when every requested fixture day finished with no crash or connection loss."""
    if failure_reason:
        return 0.0
    if not names or len(days) != len(names):
        return 0.0
    if any(day.error for day in days):
        return 0.0
    return 1.0


def _format_failure(exc: BaseException) -> str:
    """Exception type only — never copy OCR, prompts, or window titles into reports."""
    return type(exc).__name__


def _failed_day(name: str, exc: BaseException) -> DayEval:
    return DayEval(
        name=name,
        date="",
        metrics={},
        derived_first={},
        derived_second={},
        summary_present=False,
        identical_counts=False,
        error=_format_failure(exc),
    )


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
        "worker_plus_model_peak_bytes": report.worker_plus_model_peak_bytes,
        "worker_peak_bytes": report.worker_peak_bytes,
        "failure_reason": report.failure_reason,
        "latency": report.latency,
        "profile": report.profile,
        "profile_gate": report.profile_gate,
        "headroom": report.headroom,
        "completed_without_kill": report.completed_without_kill,
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
                "consolidate_s": day.consolidate_s,
                "skipped_understand": day.skipped_understand,
                "error": day.error,
            }
            for day in report.days
        ],
    }
    (out_dir / "report.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    (out_dir / "report.md").write_text(render_markdown(report), encoding="utf-8")
