"""Day consolidation worker (plan §3.7 steps 1–10, D8, D12).

Idempotent by ``(working_day_id, pipeline_version)``: a re-run deletes the
previous derived rows in one transaction and inserts replacements. Daily
narrative (step 7) cites claim ids inline; a post-processor strips invalid
citations. Achievement candidates are rule-based: observed outcomes with
change evidence, never ``inferred`` alone.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from pydantic import BaseModel
from sqlcipher3 import dbapi2 as sqlcipher

from watson.config.schema import (
    DEFAULT_DAY_END,
    DEFAULT_RECOVERY_DEADLINE_HOURS,
    DEFAULT_SEGMENT_CLOSE_AFTER_SECONDS,
    DEFAULT_SPOOL_HARD_LIMIT_HOURS,
    DEFAULT_TIER2_GRACE_DAYS,
    IntelligenceConfig,
    PipelineConfig,
    WatsonConfig,
)
from watson.intelligence.citations import (
    VerifiedNarrative,
    expand_index_citations,
    verify_narrative,
)
from watson.intelligence.prompts import load_prompt
from watson.intelligence.runtime import (
    UNDERSTAND_JOB_KIND,
    EmbeddingRuntime,
    JobFailure,
    LanguageRuntime,
    Message,
    RuntimeBundle,
    RuntimeUnavailable,
    StructuredOutputError,
    build_runtime,
    hold_if_unavailable,
)
from watson.intelligence.runtime.fake import FakeRuntime
from watson.intelligence.schemas.consolidation import (
    CLUSTERING_JSON_SCHEMA,
    DAILY_SUMMARY_JSON_SCHEMA,
    DAY_EXTRACT_JSON_SCHEMA,
    ClusteringOutput,
    DailySummaryOutput,
    DayExtractOutput,
)
from watson.intelligence.structured import complete_structured
from watson.memory.db import iso_now
from watson.memory.ids import new_id
from watson.pipeline.frames import PIPELINE_VERSION
from watson.pipeline.jobs import Job, JobLeaseError, JobState, claim, complete, enqueue, fail
from watson.pipeline.segmentation import close_open_segments
from watson.pipeline.understanding import (
    drain_understand_jobs,
    eligible_segment_ids,
    enqueue_eligible_understand_jobs,
)
from watson.privacy.audit import KIND_CONSOLIDATED, record_audit
from watson.privacy.retention import sweep_tier2
from watson.privacy.spoolsweep import sweep_spool

log = logging.getLogger("watson.pipeline.consolidation")

CONSOLIDATE_JOB_KIND = "consolidate"
WORKER_ID = "watson-consolidate"
CLUSTER_PROMPT_TASK = "clustering"
EXTRACT_PROMPT_TASK = "day_extract"
SUMMARY_PROMPT_TASK = "daily_summary"
PROMPT_VERSION = "v1"
TERMINAL_FRAME_STATES = frozenset(
    {"extracted", "deleted", "failed", "deleted_unprocessed", "skipped"}
)
_DERIVED_TABLES = (
    "activities",
    "decisions",
    "commitments",
    "achievement_candidates",
    "entity_history",
    "daily_summaries",
)


class ConsolidationWait(Exception):
    """Frames are still captured and the recovery deadline has not passed."""


class ConsolidationError(JobFailure):
    """A consolidation step failed. The day is marked ``failed``."""

    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message, retryable=retryable)


@dataclass(frozen=True)
class ConsolidateSettings:
    pipeline_version: int = PIPELINE_VERSION
    day_end: str = DEFAULT_DAY_END
    recovery_deadline_hours: int = DEFAULT_RECOVERY_DEADLINE_HOURS
    close_after_seconds: int = DEFAULT_SEGMENT_CLOSE_AFTER_SECONDS
    tier2_grace_days: int = DEFAULT_TIER2_GRACE_DAYS
    spool_hard_limit_hours: int = DEFAULT_SPOOL_HARD_LIMIT_HOURS
    model_id: str = "fake"

    @classmethod
    def from_config(
        cls,
        config: WatsonConfig | PipelineConfig | IntelligenceConfig | None,
        *,
        model_id: str | None = None,
    ) -> ConsolidateSettings:
        if isinstance(config, WatsonConfig):
            return cls(
                day_end=str(config.pipeline.day_end),
                recovery_deadline_hours=int(config.pipeline.recovery_deadline_hours),
                close_after_seconds=int(config.segmentation.close_after_seconds),
                tier2_grace_days=int(config.retention.tier2_grace_days),
                spool_hard_limit_hours=int(config.retention.spool_hard_limit_hours),
                model_id=model_id or config.intelligence.llm_model,
            )
        if isinstance(config, PipelineConfig):
            return cls(
                day_end=str(config.day_end),
                recovery_deadline_hours=int(config.recovery_deadline_hours),
                model_id=model_id or "fake",
            )
        return cls(model_id=model_id or "fake")


def consolidate_idempotency_key(
    working_day_id: str, pipeline_version: int = PIPELINE_VERSION
) -> str:
    return f"consolidate:{working_day_id}:{pipeline_version}"


def parse_day_end(day_end: str) -> tuple[int, int]:
    """Parse ``HH:MM`` (00:00–23:59)."""
    parts = day_end.split(":")
    if len(parts) != 2:
        raise ConsolidationError(f"invalid day_end {day_end!r}")
    try:
        hour = int(parts[0])
        minute = int(parts[1])
    except ValueError as exc:
        raise ConsolidationError(f"invalid day_end {day_end!r}") from exc
    if hour < 0 or hour > 23 or minute < 0 or minute > 59:
        raise ConsolidationError(f"invalid day_end {day_end!r}")
    return hour, minute


def day_end_at(date: str, day_end: str, *, tz: Any = UTC) -> datetime:
    hour, minute = parse_day_end(day_end)
    year, month, day = (int(part) for part in date.split("-"))
    zone = tz if tz is not None else UTC
    return datetime(year, month, day, hour, minute, tzinfo=zone)


def due_working_day_ids(
    conn: sqlcipher.Connection,
    *,
    now: datetime,
    day_end: str = DEFAULT_DAY_END,
) -> list[str]:
    """Open/closing/failed days whose configured day-end has passed."""
    moment = _aware(now)
    rows = conn.execute(
        """
        SELECT id, date, state FROM working_days
        WHERE state IN ('open', 'closing', 'failed')
        ORDER BY date, id
        """
    ).fetchall()
    due: list[str] = []
    for row in rows:
        end = day_end_at(str(row["date"]), day_end, tz=moment.tzinfo)
        if moment >= end:
            due.append(str(row["id"]))
    return due


def enqueue_catch_up(
    conn: sqlcipher.Connection,
    *,
    now: datetime | None = None,
    day_end: str = DEFAULT_DAY_END,
    pipeline_version: int = PIPELINE_VERSION,
) -> int:
    """Enqueue or requeue one consolidate job per due day. Idempotent."""
    moment = _aware(now)
    added = 0
    for day_id in due_working_day_ids(conn, now=moment, day_end=day_end):
        requeue_understand_jobs_for_day(conn, day_id, pipeline_version=pipeline_version, now=moment)
        if ensure_consolidate_job(conn, day_id, pipeline_version=pipeline_version, now=moment):
            added += 1
    return added


def ensure_consolidate_job(
    conn: sqlcipher.Connection,
    working_day_id: str,
    *,
    pipeline_version: int = PIPELINE_VERSION,
    now: datetime | None = None,
) -> bool:
    """Enqueue or return a dead consolidate job to pending. True if newly pending."""
    moment = _aware(now)
    key = consolidate_idempotency_key(working_day_id, pipeline_version)
    row = conn.execute("SELECT * FROM jobs WHERE idempotency_key = ?", (key,)).fetchone()
    if row is None:
        enqueue(
            conn,
            kind=CONSOLIDATE_JOB_KIND,
            payload={"working_day_id": working_day_id, "pipeline_version": pipeline_version},
            idempotency_key=key,
            now=moment,
        )
        return True
    job = Job.from_row(row)
    if job.state in (JobState.PENDING, JobState.CLAIMED):
        return False
    day = _load_day(conn, working_day_id)
    if (
        job.state is JobState.COMPLETED
        and day is not None
        and str(day.get("state")) == "consolidated"
    ):
        return False
    _reset_job(conn, job.id, now=moment)
    return True


def requeue_understand_jobs_for_day(
    conn: sqlcipher.Connection,
    working_day_id: str,
    *,
    pipeline_version: int = PIPELINE_VERSION,
    close_after_seconds: int = DEFAULT_SEGMENT_CLOSE_AFTER_SECONDS,
    now: datetime | None = None,
) -> int:
    """Return failed (or completed-without-observation) understand jobs to pending."""
    moment = _aware(now)
    reset = 0
    for job in _understand_jobs_for_day(conn, working_day_id, pipeline_version):
        if job.state is JobState.PENDING or job.state is JobState.CLAIMED:
            continue
        segment_id = str(job.payload.get("segment_id") or "")
        if job.state is JobState.COMPLETED and segment_id:
            seen = conn.execute(
                "SELECT 1 FROM observations WHERE segment_id = ? AND pipeline_version = ?",
                (segment_id, pipeline_version),
            ).fetchone()
            if seen is not None:
                continue
        _reset_job(conn, job.id, now=moment)
        reset += 1
    enqueue_eligible_understand_jobs(
        conn,
        pipeline_version=pipeline_version,
        close_after_seconds=close_after_seconds,
        working_day_id=working_day_id,
        now=moment,
    )
    return reset


def retry_working_day(
    conn: sqlcipher.Connection,
    working_day_id: str,
    *,
    runtime: LanguageRuntime | None = None,
    embedding: EmbeddingRuntime | None = None,
    spool_dir: Path | None = None,
    now: datetime | None = None,
    settings: ConsolidateSettings | None = None,
) -> str:
    """Dashboard Retry: re-enqueue dead jobs and re-run consolidation (§3.7 / §7.4)."""
    cfg = settings or ConsolidateSettings()
    moment = _aware(now)
    requeue_understand_jobs_for_day(
        conn,
        working_day_id,
        pipeline_version=cfg.pipeline_version,
        close_after_seconds=cfg.close_after_seconds,
        now=moment,
    )
    ensure_consolidate_job(conn, working_day_id, pipeline_version=cfg.pipeline_version, now=moment)
    return consolidate_working_day(
        conn,
        working_day_id,
        runtime=runtime,
        embedding=embedding,
        spool_dir=spool_dir,
        now=moment,
        settings=cfg,
    )


def catch_up_on_launch(
    conn: sqlcipher.Connection,
    *,
    runtime: LanguageRuntime | None = None,
    embedding: EmbeddingRuntime | None = None,
    config: WatsonConfig | None = None,
    spool_dir: Path | None = None,
    now: datetime | None = None,
) -> int:
    """Enqueue due days and drain consolidate jobs (D12 catch-up)."""
    settings = ConsolidateSettings.from_config(config)
    enqueue_catch_up(
        conn,
        now=now,
        day_end=settings.day_end,
        pipeline_version=settings.pipeline_version,
    )
    return drain_consolidate_jobs(
        conn,
        runtime=runtime,
        embedding=embedding,
        config=config,
        spool_dir=spool_dir,
        now=now,
    )


def drain_consolidate_jobs(
    conn: sqlcipher.Connection,
    *,
    runtime: LanguageRuntime | None = None,
    embedding: EmbeddingRuntime | None = None,
    config: WatsonConfig | None = None,
    spool_dir: Path | None = None,
    worker_id: str = WORKER_ID,
    limit: int = 10_000,
    now: datetime | None = None,
) -> int:
    """Claim and complete at most ``limit`` consolidate jobs."""
    if limit < 1:
        raise ValueError("consolidate batch limit must be >= 1")
    language, embedder, settings = _resolve_adapters(
        conn, runtime=runtime, embedding=embedding, config=config, now=now
    )
    if language is None or embedder is None:
        return 0
    spool = spool_dir if spool_dir is not None else _spool_from_config(config)
    completed = 0
    while completed < limit:
        job = claim(conn, worker_id=worker_id, kind=CONSOLIDATE_JOB_KIND, now=now)
        if job is None:
            break
        try:
            consolidate_job(
                conn,
                job,
                language,
                embedder,
                settings=settings,
                spool_dir=spool,
                now=now,
            )
            complete(conn, job.id, worker_id=worker_id, now=now)
            completed += 1
        except JobLeaseError:
            raise
        except ConsolidationWait as exc:
            _hold_waiting_job(conn, job, worker_id, exc, now=now)
            break
        except RuntimeUnavailable as exc:
            _hold_waiting_job(conn, job, worker_id, exc, now=now)
            break
        except JobFailure as exc:
            fail(conn, job.id, type(exc).__name__, worker_id=worker_id, now=now)
            if not exc.retryable:
                continue
            break
        except (OSError, ValueError, RuntimeError, sqlcipher.DatabaseError) as exc:
            fail(conn, job.id, type(exc).__name__, worker_id=worker_id, now=now)
            raise
    if completed:
        log.info("drained consolidate jobs", extra={"completed": completed, "limit": limit})
    return completed


def consolidate_job(
    conn: sqlcipher.Connection,
    job: Job,
    language: LanguageRuntime,
    embedding: EmbeddingRuntime,
    *,
    settings: ConsolidateSettings | None = None,
    spool_dir: Path | None = None,
    now: datetime | None = None,
) -> str:
    cfg = settings or ConsolidateSettings()
    working_day_id = str(job.payload.get("working_day_id") or "")
    if not working_day_id:
        raise ConsolidationError("consolidate job is missing working_day_id")
    pipeline_version = int(job.payload.get("pipeline_version") or cfg.pipeline_version)
    return consolidate_working_day(
        conn,
        working_day_id,
        runtime=language,
        embedding=embedding,
        spool_dir=spool_dir,
        now=now,
        settings=ConsolidateSettings(
            pipeline_version=pipeline_version,
            day_end=cfg.day_end,
            recovery_deadline_hours=cfg.recovery_deadline_hours,
            close_after_seconds=cfg.close_after_seconds,
            tier2_grace_days=cfg.tier2_grace_days,
            spool_hard_limit_hours=cfg.spool_hard_limit_hours,
            model_id=cfg.model_id,
        ),
    )


def consolidate_working_day(
    conn: sqlcipher.Connection,
    working_day_id: str,
    *,
    runtime: LanguageRuntime | None = None,
    embedding: EmbeddingRuntime | None = None,
    spool_dir: Path | None = None,
    now: datetime | None = None,
    day_end: str | None = None,
    recovery_deadline_hours: int | None = None,
    pipeline_version: int | None = None,
    settings: ConsolidateSettings | None = None,
) -> str:
    """Run §3.7 steps 1–10 except daily narrative. Returns the day id."""
    cfg = settings or ConsolidateSettings(
        pipeline_version=pipeline_version or PIPELINE_VERSION,
        day_end=day_end or DEFAULT_DAY_END,
        recovery_deadline_hours=(
            DEFAULT_RECOVERY_DEADLINE_HOURS
            if recovery_deadline_hours is None
            else recovery_deadline_hours
        ),
    )
    language = runtime if runtime is not None else FakeRuntime()
    embedder: EmbeddingRuntime = embedding if embedding is not None else _as_embedder(language)
    moment = _aware(now)
    try:
        day = _load_day(conn, working_day_id)
        if day is None:
            raise ConsolidationError(f"working day {working_day_id} is missing")
        _set_day_state(conn, working_day_id, "closing", now=moment)
        _confirm_frames(conn, day, now=moment, settings=cfg)
        close_open_segments(
            conn,
            working_day_id=working_day_id,
            close_after_seconds=cfg.close_after_seconds,
        )
        requeue_understand_jobs_for_day(
            conn,
            working_day_id,
            pipeline_version=cfg.pipeline_version,
            close_after_seconds=cfg.close_after_seconds,
            now=moment,
        )
        enqueue_eligible_understand_jobs(
            conn,
            pipeline_version=cfg.pipeline_version,
            close_after_seconds=cfg.close_after_seconds,
            working_day_id=working_day_id,
            now=moment,
        )
        drain_understand_jobs(
            conn,
            runtime=language,
            embedding=embedder,
            now=moment,
        )
        pending = eligible_segment_ids(
            conn,
            pipeline_version=cfg.pipeline_version,
            close_after_seconds=cfg.close_after_seconds,
            working_day_id=working_day_id,
        )
        if pending:
            if _understand_is_held(
                conn,
                working_day_id,
                pipeline_version=cfg.pipeline_version,
                close_after_seconds=cfg.close_after_seconds,
            ):
                raise ConsolidationWait("understanding held")
            raise ConsolidationError("pending understanding")
        counts = _replace_derived(
            conn,
            working_day_id,
            language,
            settings=cfg,
            now=moment,
        )
        _verify_persistence(
            conn,
            working_day_id,
            pipeline_version=cfg.pipeline_version,
            expected=counts,
        )
        _apply_retention_and_sweep(
            conn,
            working_day_id,
            settings=cfg,
            spool_dir=spool_dir,
            now=moment,
            counts=counts,
        )
        _set_day_state(
            conn,
            working_day_id,
            "consolidated",
            consolidated_at=iso_now(moment),
            now=moment,
        )
        log.info(
            "consolidated working day",
            extra={"working_day_id": working_day_id, **counts},
        )
        return working_day_id
    except ConsolidationWait as exc:
        _hold_day(conn, working_day_id, exc, now=moment)
        raise
    except RuntimeUnavailable as exc:
        _hold_day(conn, working_day_id, exc, now=moment)
        raise
    except Exception as exc:
        _set_day_state(
            conn,
            working_day_id,
            "failed",
            error=type(exc).__name__,
            now=moment,
        )
        raise


def derived_counts(
    conn: sqlcipher.Connection, working_day_id: str, pipeline_version: int = PIPELINE_VERSION
) -> dict[str, int]:
    """Row counts for the derived tables of one ``(day, pipeline_version)``."""
    out: dict[str, int] = {}
    for table in _DERIVED_TABLES:
        row = conn.execute(
            f"SELECT COUNT(*) FROM {table} WHERE working_day_id = ? AND pipeline_version = ?",
            (working_day_id, pipeline_version),
        ).fetchone()
        key = "achievements" if table == "achievement_candidates" else table
        if table == "entity_history":
            key = "history"
        if table == "daily_summaries":
            key = "summaries"
        out[key] = int(row[0])
    return out


def _confirm_frames(
    conn: sqlcipher.Connection,
    day: dict[str, Any],
    *,
    now: datetime,
    settings: ConsolidateSettings,
) -> None:
    states = tuple(sorted(TERMINAL_FRAME_STATES))
    placeholders = ",".join("?" * len(states))
    pending = int(
        conn.execute(
            f"""
            SELECT COUNT(*) FROM frames
            WHERE working_day_id = ?
              AND state NOT IN ({placeholders})
            """,
            (day["id"], *states),
        ).fetchone()[0]
    )
    if pending == 0:
        return
    end = day_end_at(str(day["date"]), settings.day_end, tz=now.tzinfo)
    deadline = end + timedelta(hours=settings.recovery_deadline_hours)
    if now < deadline:
        raise ConsolidationWait("frames still captured")
    stamp = iso_now(now)
    conn.execute(
        """
        UPDATE working_days
        SET consolidation_gap = ?, updated_at = ?
        WHERE id = ?
        """,
        (f"captured={pending}", stamp, day["id"]),
    )
    conn.commit()


def _replace_derived(
    conn: sqlcipher.Connection,
    working_day_id: str,
    language: LanguageRuntime,
    *,
    settings: ConsolidateSettings,
    now: datetime,
) -> dict[str, int]:
    observations = _load_observations(conn, working_day_id, settings.pipeline_version)
    claims = _load_claims(conn, working_day_id, settings.pipeline_version)
    cluster_prompt = load_prompt(CLUSTER_PROMPT_TASK, PROMPT_VERSION)
    extract_prompt = load_prompt(EXTRACT_PROMPT_TASK, PROMPT_VERSION)
    summary_prompt = load_prompt(SUMMARY_PROMPT_TASK, PROMPT_VERSION)
    clustering = (
        _cluster_observations(language, observations, cluster_prompt.text)
        if observations
        else ClusteringOutput(activities=[])
    )
    extract = (
        _extract_day(language, claims, _load_entities(conn), extract_prompt.text)
        if claims
        else DayExtractOutput(entity_deltas=[], decisions=[], commitments=[])
    )
    raw_summary = (
        _summarize_day(language, claims, summary_prompt.text)
        if claims
        else DailySummaryOutput(paragraphs=[])
    )
    verified = verify_narrative(
        expand_index_citations(
            [
                {"text": item.text, "claim_ids": list(item.claim_ids)}
                for item in raw_summary.paragraphs
            ],
            claims,
        ),
        claims,
    )
    stamp = iso_now(now)
    try:
        _delete_derived(conn, working_day_id, settings.pipeline_version)
        _insert_activities(
            conn,
            working_day_id,
            clustering,
            observations,
            prompt_version=cluster_prompt.prompt_version,
            pipeline_version=settings.pipeline_version,
            stamp=stamp,
        )
        _insert_extract(
            conn,
            working_day_id,
            extract,
            claims,
            prompt_version=extract_prompt.prompt_version,
            pipeline_version=settings.pipeline_version,
            stamp=stamp,
        )
        _insert_achievements(
            conn,
            working_day_id,
            pipeline_version=settings.pipeline_version,
            stamp=stamp,
        )
        _insert_summary(
            conn,
            working_day_id,
            verified,
            prompt_version=summary_prompt.prompt_version,
            pipeline_version=settings.pipeline_version,
            stamp=stamp,
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return derived_counts(conn, working_day_id, settings.pipeline_version)


def _cluster_observations(
    language: LanguageRuntime,
    observations: list[dict[str, Any]],
    system: str,
) -> ClusteringOutput:
    numbered = "\n".join(
        f"{index}. {row['description']}" for index, row in enumerate(observations, start=1)
    )
    messages: list[Message] = [
        {"role": "system", "content": system},
        {
            "role": "user",
            "content": f"Cluster these observations into activities.\n\n{numbered}",
        },
    ]
    return _structured(language, messages, ClusteringOutput, CLUSTERING_JSON_SCHEMA, "clustering")


def _extract_day(
    language: LanguageRuntime,
    claims: list[dict[str, Any]],
    entities: list[dict[str, Any]],
    system: str,
) -> DayExtractOutput:
    claim_lines = "\n".join(
        f"{index}. [{row['kind']}/{row['assertion']}] {row['text']}"
        for index, row in enumerate(claims, start=1)
    )
    entity_lines = (
        "\n".join(f"- {row['type']}: {row['canonical_name']}" for row in entities) or "(none)"
    )
    messages: list[Message] = [
        {"role": "system", "content": system},
        {
            "role": "user",
            "content": (
                "Extract cross-segment decisions, commitments, and entity deltas.\n\n"
                f"Entities:\n{entity_lines}\n\nClaims:\n{claim_lines}\n"
            ),
        },
    ]
    return _structured(language, messages, DayExtractOutput, DAY_EXTRACT_JSON_SCHEMA, "day extract")


def _summarize_day(
    language: LanguageRuntime,
    claims: list[dict[str, Any]],
    system: str,
) -> DailySummaryOutput:
    claim_lines = "\n".join(
        f"[c:{row['id']}] ({row['kind']}/{row['assertion']}) {row['text']}" for row in claims
    )
    messages: list[Message] = [
        {"role": "system", "content": system},
        {
            "role": "user",
            "content": (
                "Write the daily narrative from these claims. "
                "Cite each used claim inline as [c:<id>].\n\n"
                f"Claims:\n{claim_lines}\n"
            ),
        },
    ]
    return _structured(
        language, messages, DailySummaryOutput, DAILY_SUMMARY_JSON_SCHEMA, "daily summary"
    )


def _structured[T: BaseModel](
    language: LanguageRuntime,
    messages: list[Message],
    model: type[T],
    schema: dict[str, Any],
    label: str,
) -> T:
    try:
        return complete_structured(language, messages, model, schema=schema)
    except (StructuredOutputError, RuntimeUnavailable, JobFailure):
        raise
    except Exception as exc:
        raise ConsolidationError(f"{label} complete failed", retryable=True) from exc


def _delete_derived(conn: sqlcipher.Connection, working_day_id: str, pipeline_version: int) -> None:
    for table in _DERIVED_TABLES:
        conn.execute(
            f"DELETE FROM {table} WHERE working_day_id = ? AND pipeline_version = ?",
            (working_day_id, pipeline_version),
        )


def _insert_activities(
    conn: sqlcipher.Connection,
    working_day_id: str,
    clustering: ClusteringOutput,
    observations: list[dict[str, Any]],
    *,
    prompt_version: str,
    pipeline_version: int,
    stamp: str,
) -> None:
    obs_ids = [str(row["id"]) for row in observations]
    for activity in clustering.activities:
        chosen = _pick_indexes(activity.observation_indexes, obs_ids)
        if not chosen:
            continue
        conn.execute(
            """
            INSERT INTO activities (
                id, created_at, updated_at, working_day_id, title, summary,
                observation_ids, prompt_version, pipeline_version
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                new_id(),
                stamp,
                stamp,
                working_day_id,
                activity.title,
                activity.summary,
                json.dumps(chosen),
                prompt_version,
                pipeline_version,
            ),
        )


def _insert_extract(
    conn: sqlcipher.Connection,
    working_day_id: str,
    extract: DayExtractOutput,
    claims: list[dict[str, Any]],
    *,
    prompt_version: str,
    pipeline_version: int,
    stamp: str,
) -> None:
    claim_ids = [str(row["id"]) for row in claims]
    seen_entities: set[str] = set()
    for delta in extract.entity_deltas:
        grounded = _pick_indexes(delta.claim_indexes, claim_ids)
        if not grounded:
            continue
        entity_id = _match_entity(conn, delta.entity_name, delta.entity_type)
        if entity_id is None or entity_id in seen_entities:
            continue
        seen_entities.add(entity_id)
        conn.execute(
            """
            INSERT INTO entity_history (
                id, created_at, updated_at, entity_id, working_day_id, delta_text,
                claim_ids, prompt_version, pipeline_version
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                new_id(),
                stamp,
                stamp,
                entity_id,
                working_day_id,
                delta.delta_text,
                json.dumps(grounded),
                prompt_version,
                pipeline_version,
            ),
        )
    for item in extract.decisions:
        grounded = _pick_indexes(item.claim_indexes, claim_ids)
        if not grounded:
            continue
        conn.execute(
            """
            INSERT INTO decisions (
                id, created_at, updated_at, working_day_id, text, claim_ids,
                prompt_version, pipeline_version
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                new_id(),
                stamp,
                stamp,
                working_day_id,
                item.text,
                json.dumps(grounded),
                prompt_version,
                pipeline_version,
            ),
        )
    for item in extract.commitments:
        grounded = _pick_indexes(item.claim_indexes, claim_ids)
        if not grounded:
            continue
        status = item.status or "open"
        conn.execute(
            """
            INSERT INTO commitments (
                id, created_at, updated_at, working_day_id, text, status, due_hint,
                claim_ids, prompt_version, pipeline_version
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                new_id(),
                stamp,
                stamp,
                working_day_id,
                item.text,
                status,
                item.due_hint,
                json.dumps(grounded),
                prompt_version,
                pipeline_version,
            ),
        )


def _insert_achievements(
    conn: sqlcipher.Connection,
    working_day_id: str,
    *,
    pipeline_version: int,
    stamp: str,
) -> None:
    """Observed outcomes/achievements with change evidence. Never inferred-only."""
    rows = conn.execute(
        """
        SELECT c.id, c.text
        FROM claims c
        JOIN observations o ON o.id = c.observation_id
        JOIN segments s ON s.id = o.segment_id
        WHERE c.working_day_id = ?
          AND c.pipeline_version = ?
          AND c.kind IN ('outcome', 'achievement')
          AND c.assertion = 'observed'
          AND s.line_changes > 0
          AND EXISTS (SELECT 1 FROM claim_evidence e WHERE e.claim_id = c.id)
        ORDER BY c.created_at, c.id
        """,
        (working_day_id, pipeline_version),
    ).fetchall()
    for row in rows:
        conn.execute(
            """
            INSERT INTO achievement_candidates (
                id, created_at, updated_at, working_day_id, claim_id, text, pipeline_version
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                new_id(),
                stamp,
                stamp,
                working_day_id,
                str(row["id"]),
                str(row["text"]),
                pipeline_version,
            ),
        )


def _insert_summary(
    conn: sqlcipher.Connection,
    working_day_id: str,
    verified: VerifiedNarrative,
    *,
    prompt_version: str,
    pipeline_version: int,
    stamp: str,
) -> None:
    conn.execute(
        """
        INSERT INTO daily_summaries (
            id, created_at, updated_at, working_day_id, narrative_md, sections_json,
            claim_ids, prompt_version, pipeline_version
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            new_id(),
            stamp,
            stamp,
            working_day_id,
            verified.narrative_md,
            json.dumps(verified.sections_json()),
            json.dumps(list(verified.claim_ids)),
            prompt_version,
            pipeline_version,
        ),
    )


def _reread_day(conn: sqlcipher.Connection, working_day_id: str) -> dict[str, Any] | None:
    """Re-read the working-day row after checkpoint. None if the row is gone."""
    row = conn.execute(
        "SELECT id, state FROM working_days WHERE id = ?",
        (working_day_id,),
    ).fetchone()
    if row is None:
        return None
    return {"id": str(row["id"]), "state": str(row["state"])}


def _verify_persistence(
    conn: sqlcipher.Connection,
    working_day_id: str,
    *,
    pipeline_version: int,
    expected: dict[str, int],
) -> None:
    conn.execute("PRAGMA wal_checkpoint(FULL)")
    day = _reread_day(conn, working_day_id)
    if day is None:
        raise ConsolidationError("working day missing after wal_checkpoint")
    seen = derived_counts(conn, working_day_id, pipeline_version)
    if seen != expected:
        raise ConsolidationError("derived rows missing after wal_checkpoint")


def _apply_retention_and_sweep(
    conn: sqlcipher.Connection,
    working_day_id: str,
    *,
    settings: ConsolidateSettings,
    spool_dir: Path | None,
    now: datetime,
    counts: dict[str, int],
) -> None:
    stamp = iso_now(now)
    expires = iso_now(now + timedelta(days=settings.tier2_grace_days))
    conn.execute(
        """
        UPDATE frame_text
        SET expires_at = ?, updated_at = ?
        WHERE frame_id IN (SELECT id FROM frames WHERE working_day_id = ?)
        """,
        (expires, stamp, working_day_id),
    )
    conn.execute(
        """
        UPDATE segments
        SET expires_at = ?, updated_at = ?
        WHERE working_day_id = ?
        """,
        (expires, stamp, working_day_id),
    )
    conn.commit()
    sweep_tier2(conn, now=now)
    if spool_dir is not None:
        sweep_spool(conn, spool_dir, now=now, hard_limit_hours=settings.spool_hard_limit_hours)
    record_audit(
        conn,
        kind=KIND_CONSOLIDATED,
        detail={
            "working_day_id": working_day_id,
            "activities": counts["activities"],
            "decisions": counts["decisions"],
            "commitments": counts["commitments"],
            "achievements": counts["achievements"],
            "history": counts["history"],
            "summaries": counts["summaries"],
        },
        commit=True,
    )


def _set_day_state(
    conn: sqlcipher.Connection,
    working_day_id: str,
    state: str,
    *,
    error: str | None = None,
    consolidated_at: str | None = None,
    now: datetime | None = None,
) -> None:
    stamp = iso_now(now)
    ended = stamp if state in {"consolidated", "failed", "closing"} else None
    conn.execute(
        """
        UPDATE working_days
        SET state = ?,
            consolidation_error = ?,
            consolidated_at = ?,
            ended_at = COALESCE(ended_at, ?),
            updated_at = ?
        WHERE id = ?
        """,
        (state, error, consolidated_at, ended, stamp, working_day_id),
    )
    conn.commit()


def _load_day(conn: sqlcipher.Connection, working_day_id: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM working_days WHERE id = ?", (working_day_id,)).fetchone()
    if row is None:
        return None
    return {key: row[key] for key in row.keys()}


def _load_observations(
    conn: sqlcipher.Connection, working_day_id: str, pipeline_version: int
) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT o.id, o.description
        FROM observations o
        JOIN segments s ON s.id = o.segment_id
        WHERE s.working_day_id = ? AND o.pipeline_version = ?
        ORDER BY s.started_at, o.id
        """,
        (working_day_id, pipeline_version),
    ).fetchall()
    return [{"id": str(row["id"]), "description": str(row["description"])} for row in rows]


def _load_claims(
    conn: sqlcipher.Connection, working_day_id: str, pipeline_version: int
) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT id, kind, assertion, text
        FROM claims
        WHERE working_day_id = ? AND pipeline_version = ?
        ORDER BY created_at, id
        """,
        (working_day_id, pipeline_version),
    ).fetchall()
    return [
        {
            "id": str(row["id"]),
            "kind": str(row["kind"]),
            "assertion": str(row["assertion"]),
            "text": str(row["text"]),
        }
        for row in rows
    ]


def _load_entities(conn: sqlcipher.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT type, canonical_name FROM entities
        WHERE merged_into_id IS NULL
        ORDER BY type, canonical_name, id
        """
    ).fetchall()
    return [
        {"type": str(row["type"]), "canonical_name": str(row["canonical_name"])} for row in rows
    ]


def _match_entity(conn: sqlcipher.Connection, name: str, typ: str) -> str | None:
    needle = name.casefold().strip()
    if not needle:
        return None
    row = conn.execute(
        """
        SELECT e.id
        FROM entities e
        LEFT JOIN entity_aliases a ON a.entity_id = e.id
        WHERE e.type = ?
          AND e.merged_into_id IS NULL
          AND (lower(e.canonical_name) = ? OR lower(a.alias) = ?)
        ORDER BY e.provisional ASC, e.canonical_name, e.id
        LIMIT 1
        """,
        (typ, needle, needle),
    ).fetchone()
    return None if row is None else str(row["id"])


def _hold_day(
    conn: sqlcipher.Connection,
    working_day_id: str,
    exc: BaseException,
    *,
    now: datetime | None,
) -> None:
    """§7.4: stay closing with a reason; do not mark the day failed."""
    _set_day_state(conn, working_day_id, "closing", error=type(exc).__name__, now=now)


def _reset_job(conn: sqlcipher.Connection, job_id: str, *, now: datetime | None) -> None:
    stamp = iso_now(now)
    conn.execute(
        """
        UPDATE jobs
        SET state = ?, attempts = 0, last_error = NULL, claimed_by = NULL,
            leased_until = NULL, run_after = ?, updated_at = ?
        WHERE id = ?
        """,
        (JobState.PENDING.value, stamp, stamp, job_id),
    )
    conn.commit()


def _understand_jobs_for_day(
    conn: sqlcipher.Connection, working_day_id: str, pipeline_version: int
) -> list[Job]:
    segment_ids = {
        str(row[0])
        for row in conn.execute(
            "SELECT id FROM segments WHERE working_day_id = ?", (working_day_id,)
        )
    }
    if not segment_ids:
        return []
    rows = conn.execute("SELECT * FROM jobs WHERE kind = ?", (UNDERSTAND_JOB_KIND,)).fetchall()
    out: list[Job] = []
    for row in rows:
        job = Job.from_row(row)
        if str(job.payload.get("segment_id") or "") not in segment_ids:
            continue
        if int(job.payload.get("pipeline_version") or PIPELINE_VERSION) != pipeline_version:
            continue
        out.append(job)
    return out


def _understand_is_held(
    conn: sqlcipher.Connection,
    working_day_id: str,
    *,
    pipeline_version: int,
    close_after_seconds: int,
) -> bool:
    # This day's eligible segments only. Another day's pending work must not
    # hold a broken day as ConsolidationWait forever.
    pending = set(
        eligible_segment_ids(
            conn,
            pipeline_version=pipeline_version,
            close_after_seconds=close_after_seconds,
            working_day_id=working_day_id,
        )
    )
    if not pending:
        return False
    by_segment = {
        str(job.payload.get("segment_id") or ""): job
        for job in _understand_jobs_for_day(conn, working_day_id, pipeline_version)
    }
    for segment_id in pending:
        job = by_segment.get(segment_id)
        if job is None or job.state is not JobState.FAILED:
            return True
    return False


def _pick_indexes(indexes: list[int], items: list[str]) -> list[str]:
    chosen: list[str] = []
    seen: set[str] = set()
    for raw in indexes:
        if 1 <= int(raw) <= len(items):
            item = items[int(raw) - 1]
            if item not in seen:
                seen.add(item)
                chosen.append(item)
    return chosen


def _resolve_adapters(
    conn: sqlcipher.Connection,
    *,
    runtime: LanguageRuntime | None,
    embedding: EmbeddingRuntime | None,
    config: WatsonConfig | IntelligenceConfig | None,
    now: datetime | None,
) -> tuple[LanguageRuntime | None, EmbeddingRuntime | None, ConsolidateSettings]:
    if runtime is not None:
        embedder: EmbeddingRuntime = embedding if embedding is not None else _as_embedder(runtime)
        model_id = getattr(runtime, "name", None)
        if isinstance(model_id, str) and model_id:
            return runtime, embedder, ConsolidateSettings.from_config(config, model_id=model_id)
        return runtime, embedder, ConsolidateSettings.from_config(config)
    if config is None:
        config = IntelligenceConfig(runtime="fake")
    if isinstance(config, WatsonConfig):
        held = hold_if_unavailable(conn, config.intelligence, now=now)
        settings = ConsolidateSettings.from_config(config)
    else:
        held = hold_if_unavailable(conn, config, now=now)
        settings = ConsolidateSettings.from_config(config)
    if held.held:
        return None, None, settings
    bundle: RuntimeBundle = build_runtime(
        config.intelligence if isinstance(config, WatsonConfig) else config
    )
    return bundle.language, bundle.embedding, settings


def _as_embedder(runtime: LanguageRuntime) -> EmbeddingRuntime:
    embed = getattr(runtime, "embed", None)
    if callable(embed):
        return runtime  # type: ignore[return-value]
    return FakeRuntime()


def _spool_from_config(config: WatsonConfig | None) -> Path | None:
    if config is None:
        return None
    return config.spool_dir


def _hold_waiting_job(
    conn: sqlcipher.Connection,
    job: Job,
    worker_id: str,
    exc: BaseException,
    *,
    now: datetime | None,
) -> None:
    stamp = iso_now(now)
    conn.execute(
        """
        UPDATE jobs
        SET state = ?, last_error = ?, claimed_by = NULL, leased_until = NULL, updated_at = ?
        WHERE id = ? AND claimed_by = ?
        """,
        (JobState.PENDING.value, type(exc).__name__, stamp, job.id, worker_id),
    )
    conn.commit()


def _aware(now: datetime | None) -> datetime:
    moment = now or datetime.now(UTC)
    if moment.tzinfo is None:
        return moment.replace(tzinfo=UTC)
    return moment.astimezone(moment.tzinfo)
