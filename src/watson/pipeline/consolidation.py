"""Day consolidation worker (plan §3.7 steps 1–10, D8, D12).

Idempotent by ``(working_day_id, pipeline_version)``: a re-run deletes the
previous derived rows in one transaction and inserts replacements. Daily
narrative (step 7) cites claim ids inline; a post-processor strips invalid
citations. Achievement candidates are rule-based: observed outcomes with
change evidence, never ``inferred`` alone. Identical texts on one
``(working_day_id, pipeline_version, project entity or NULL)`` collapse
to one candidate that keeps every supporting claim through
``achievement_candidate_claims``.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Sequence
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
    CITATION_RE,
    VerifiedNarrative,
    _cited_fallback,
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
    OutputParseError,
    RuntimeBundle,
    RuntimeCallError,
    RuntimeUnavailable,
    StructuredOutputError,
    build_runtime,
    end_runtime_phase,
    hold_if_unavailable,
)
from watson.intelligence.schemas.consolidation import (
    CLUSTERING_JSON_SCHEMA,
    DAILY_SUMMARY_JSON_SCHEMA,
    DAY_EXTRACT_JSON_SCHEMA,
    ActivityCluster,
    ClusteringOutput,
    DailySummaryOutput,
    DayExtractOutput,
    DealCluster,
    SummaryParagraph,
)
from watson.intelligence.structured import complete_structured
from watson.memory.corrections import apply_claim_corrections, rejected_claim_ids
from watson.memory.db import iso_now, parse_iso
from watson.memory.ids import new_id
from watson.pipeline.frames import PIPELINE_VERSION
from watson.pipeline.jobs import (
    Job,
    JobLeaseError,
    JobState,
    backoff_seconds,
    claim,
    complete,
    enqueue,
    fail,
    recover_expired_leases,
)
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
CLUSTER_PROMPT_VERSION = "v2"
EXTRACT_PROMPT_VERSION = "v1"
SUMMARY_PROMPT_VERSION = "v2"
PROMPT_VERSION = EXTRACT_PROMPT_VERSION
# Keep clustering / extract / summary user lists inside the 4096-token window.
_CLUSTER_ITEM_MAX = 120
_EXTRACT_CLAIM_MAX = 160
_SUMMARY_CLAIM_MAX = 120
_PROMPT_LIST_MAX = 3000
_UNDERSTAND_FAILED_RE = re.compile(r"understand_failed=\d+")
_GROUND_TOKEN_RE = re.compile(r"[a-z0-9]{4,}")
_GROUND_OVERLAP_MIN = 0.4
_ROSTER_CAP = 12
_SUMMARY_SLOTS = frozenset({"what_happened", "who_involved", "next_step"})
_SLOT_PREFIXES: tuple[tuple[str, str], ...] = (
    ("what happened:", "what_happened"),
    ("who involved:", "who_involved"),
    ("next step:", "next_step"),
)
_NEXT_STEP_MARKERS = (" will ", "need to", "going to", "next step")
_PEOPLE_HINTS = (" joined ", " called ", " emailed ", " met ")
TERMINAL_FRAME_STATES = frozenset(
    {"extracted", "deleted", "failed", "deleted_unprocessed", "skipped"}
)
_DERIVED_TABLES = (
    "activities",
    "deals",
    "decisions",
    "commitments",
    "achievement_candidates",
    "entity_history",
    "daily_summaries",
)
_NONE_DEAL_TITLES = frozenset(
    {
        "none",
        "none deal",
        "n/a",
        "na",
        "no deal",
        "non-deal",
        "no-deal",
        "not a deal",
        "unassigned",
    }
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
    force: bool = False,
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
    force: bool = False,
) -> int:
    """Return incomplete understand jobs to pending.

    Terminal-failed jobs keep their attempt count and stay ``failed`` unless
    ``force`` (dashboard Retry) starts a fresh attempt.
    """
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
        if job.state is JobState.FAILED and not force:
            continue
        _reset_job(conn, job.id, now=moment, preserve_attempts=not force)
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
    recover_expired_leases(conn, now=moment)
    requeue_understand_jobs_for_day(
        conn,
        working_day_id,
        pipeline_version=cfg.pipeline_version,
        close_after_seconds=cfg.close_after_seconds,
        now=moment,
        force=True,
    )
    ensure_consolidate_job(
        conn,
        working_day_id,
        pipeline_version=cfg.pipeline_version,
        now=moment,
        force=True,
    )
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
            continue
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
    moment = _aware(now)
    try:
        recover_expired_leases(conn, now=moment)
        if runtime is None:
            raise RuntimeUnavailable("intelligence runtime is unavailable")
        language = runtime
        embedder: EmbeddingRuntime = embedding if embedding is not None else _as_embedder(language)
        day = _load_day(conn, working_day_id)
        if day is None:
            raise ConsolidationError(f"working day {working_day_id} is missing")
        _set_day_state(conn, working_day_id, "closing", now=moment)
        _confirm_frames(conn, day, now=moment, settings=cfg)
        close_open_segments(
            conn,
            working_day_id=working_day_id,
            close_after_seconds=cfg.close_after_seconds,
            now=moment,
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
        if _understand_has_claimed(conn, working_day_id, pipeline_version=cfg.pipeline_version):
            raise ConsolidationWait("understanding held")
        drain_understand_jobs(
            conn,
            runtime=language,
            embedding=embedder,
            now=moment,
        )
        blocked = _understand_incomplete(
            conn,
            working_day_id,
            pipeline_version=cfg.pipeline_version,
            close_after_seconds=cfg.close_after_seconds,
        )
        if blocked == "wait":
            raise ConsolidationWait("understanding held")
        skipped = skipped_understand_segment_ids(
            conn,
            working_day_id,
            pipeline_version=cfg.pipeline_version,
            close_after_seconds=cfg.close_after_seconds,
        )
        counts = _replace_derived(
            conn,
            working_day_id,
            language,
            settings=cfg,
            now=moment,
            skipped_understand=skipped,
        )
        _record_understand_gaps(conn, working_day_id, skipped, now=moment)
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
            extra={
                "working_day_id": working_day_id,
                "skipped_understand": len(skipped),
                **counts,
            },
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
    skipped_understand: list[str] | None = None,
) -> dict[str, int]:
    observations = _load_observations(conn, working_day_id, settings.pipeline_version)
    claims = apply_claim_corrections(
        conn,
        _load_claims(conn, working_day_id, settings.pipeline_version),
        working_day_id=working_day_id,
        pipeline_version=settings.pipeline_version,
    )
    cluster_prompt = load_prompt(CLUSTER_PROMPT_TASK, CLUSTER_PROMPT_VERSION)
    extract_prompt = load_prompt(EXTRACT_PROMPT_TASK, EXTRACT_PROMPT_VERSION)
    summary_prompt = load_prompt(SUMMARY_PROMPT_TASK, SUMMARY_PROMPT_VERSION)
    clustering = (
        _cluster_observations(language, observations, cluster_prompt.text)
        if observations
        else ClusteringOutput()
    )
    entities = _load_entities(conn)
    extract = (
        _extract_day(language, claims, entities, extract_prompt.text)
        if claims
        else DayExtractOutput(entity_deltas=[], decisions=[], commitments=[])
    )
    deal_groups = _group_claims_by_deal(clustering, observations, claims)
    cite_claims = [row for _key, _title, grouped in deal_groups for row in grouped]
    roster = _day_roster(entities, observations)
    raw_summary = (
        _summarize_day(
            language,
            cite_claims,
            summary_prompt.text,
            deal_groups=deal_groups,
            roster=roster,
        )
        if deal_groups and cite_claims
        else DailySummaryOutput(paragraphs=[])
    )
    end_runtime_phase(language)
    buckets, slot_drafts, cited, unverified = _bucket_deal_paragraphs(
        raw_summary,
        deal_groups,
        working_day_id=working_day_id,
        pipeline_version=settings.pipeline_version,
        claims=claims,
    )
    skipped = skipped_understand or []
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
        inserted_deals = _insert_deals(
            conn,
            working_day_id,
            clustering,
            observations,
            prompt_version=cluster_prompt.prompt_version,
            pipeline_version=settings.pipeline_version,
            stamp=stamp,
        )
        verified = _narrative_from_stored_deals(inserted_deals, buckets, cited, unverified)
        if not verified.narrative_md.strip() and claims:
            # Claims with no stored deal group still need a cited body.
            verified = _cited_fallback(claims)
        if skipped:
            note = f"Note: {len(skipped)} segment(s) were excluded after understanding failed."
            paragraphs = (note, *verified.paragraphs)
            verified = VerifiedNarrative(
                narrative_md="\n\n".join(paragraphs),
                paragraphs=paragraphs,
                claim_ids=verified.claim_ids,
                unverified=verified.unverified,
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
            extra_sections=_summary_deal_sections(inserted_deals, slot_drafts, buckets),
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
    numbered = _trim_numbered(
        [_observation_cluster_text(row) for row in observations],
        item_max=_CLUSTER_ITEM_MAX,
        list_max=_PROMPT_LIST_MAX,
    )
    messages: list[Message] = [
        {"role": "system", "content": system},
        {
            "role": "user",
            "content": (
                "Cluster these observations into deals. "
                "Same sales opportunity across email, call, and docs is one deal. "
                "Separate companies are separate deals — do not merge Pier invoice "
                "work with Cedar onboarding. "
                "none is its own class — leave those observations out.\n\n"
                f"{numbered}"
            ),
        },
    ]
    result = _structured(language, messages, ClusteringOutput, CLUSTERING_JSON_SCHEMA, "clustering")
    if result is None:
        return _fallback_clustering(observations)
    if not result.deals and result.activities:
        # Old recordings emit activities; V1 grouping consumes them as deals.
        return ClusteringOutput(
            activities=list(result.activities),
            deals=[
                DealCluster(
                    title=item.title,
                    summary=item.summary,
                    observation_indexes=list(item.observation_indexes),
                )
                for item in result.activities
            ],
        )
    return result


def _extract_day(
    language: LanguageRuntime,
    claims: list[dict[str, Any]],
    entities: list[dict[str, Any]],
    system: str,
) -> DayExtractOutput:
    claim_lines = _trim_numbered(
        [f"[{row['kind']}/{row['assertion']}] {row['text']}" for row in claims],
        item_max=_EXTRACT_CLAIM_MAX,
        list_max=_PROMPT_LIST_MAX,
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
    result = _structured(
        language, messages, DayExtractOutput, DAY_EXTRACT_JSON_SCHEMA, "day extract"
    )
    if result is None:
        return DayExtractOutput(entity_deltas=[], decisions=[], commitments=[])
    return result


def _summarize_day(
    language: LanguageRuntime,
    claims: list[dict[str, Any]],
    system: str,
    *,
    deal_groups: list[tuple[object, str, list[dict[str, Any]]]] | None = None,
    roster: Sequence[str] | None = None,
) -> DailySummaryOutput:
    names = [name.strip() for name in (roster or ()) if str(name).strip()]
    roster_line = "Roster names: " + (", ".join(names) if names else "(none)")
    if deal_groups:
        blocks: list[str] = []
        for _key, title, grouped in deal_groups:
            lines = _trim_numbered(
                [
                    f"[c:{row['id']}] ({row['kind']}/{row['assertion']}) {row['text']}"
                    for row in grouped
                ],
                item_max=_SUMMARY_CLAIM_MAX,
                list_max=_PROMPT_LIST_MAX,
            )
            blocks.append(f"Deal: {title}\n{roster_line}\nClaims:\n{lines}")
        claim_block = "\n\n".join(blocks)
    else:
        claim_lines = _trim_numbered(
            [f"[c:{row['id']}] ({row['kind']}/{row['assertion']}) {row['text']}" for row in claims],
            item_max=_SUMMARY_CLAIM_MAX,
            list_max=_PROMPT_LIST_MAX,
        )
        claim_block = f"{roster_line}\nClaims:\n{claim_lines}"
    messages: list[Message] = [
        {"role": "system", "content": system},
        {
            "role": "user",
            "content": (
                "Write the daily narrative from these claims. "
                "Cite each used claim inline as [c:<id>]. "
                "For each deal write what happened, who was involved, and the next step "
                "from that deal's claims only.\n\n"
                f"{claim_block}\n"
            ),
        },
    ]
    result = _structured(
        language, messages, DailySummaryOutput, DAILY_SUMMARY_JSON_SCHEMA, "daily summary"
    )
    if result is None or not result.paragraphs:
        # Per-deal verify falls back from that deal's claims only.
        return DailySummaryOutput(paragraphs=[])
    return result


def _structured[T: BaseModel](
    language: LanguageRuntime,
    messages: list[Message],
    model: type[T],
    schema: dict[str, Any],
    label: str,
) -> T | None:
    try:
        return complete_structured(language, messages, model, schema=schema)
    except RuntimeUnavailable:
        raise
    except (StructuredOutputError, OutputParseError, RuntimeCallError) as exc:
        log.warning(
            "consolidation structured output failed",
            extra={"label": label, "error": type(exc).__name__},
        )
        return None
    except JobFailure:
        raise
    except Exception as exc:
        if exc.__class__.__name__ == "ReplayMiss":
            raise
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


def _insert_deals(
    conn: sqlcipher.Connection,
    working_day_id: str,
    clustering: ClusteringOutput,
    observations: list[dict[str, Any]],
    *,
    prompt_version: str,
    pipeline_version: int,
    stamp: str,
) -> list[dict[str, Any]]:
    """Persist deal clusters and assign ``segments.deal_id``. Unassigned is ``none``."""
    conn.execute(
        "UPDATE segments SET deal_id = NULL WHERE working_day_id = ?",
        (working_day_id,),
    )
    obs_ids = [str(row["id"]) for row in observations]
    obs_by_id = {str(row["id"]): row for row in observations}
    inserted: list[dict[str, Any]] = []
    assigned_obs: set[str] = set()
    for index, cluster in enumerate(clustering.deals):
        if _is_none_deal_title(cluster.title):
            continue
        chosen = _unique_observation_ids(cluster.observation_indexes, obs_ids, assigned_obs)
        if not chosen:
            continue
        assigned_obs.update(chosen)
        deal_id = new_id()
        group_key = _deal_group_key(index, cluster.title)
        created = _deal_created_at(stamp, len(inserted))
        conn.execute(
            """
            INSERT INTO deals (
                id, created_at, updated_at, working_day_id, title, summary,
                observation_ids, prompt_version, pipeline_version
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                deal_id,
                created,
                created,
                working_day_id,
                cluster.title,
                cluster.summary,
                json.dumps(chosen),
                prompt_version,
                pipeline_version,
            ),
        )
        for observation_id in chosen:
            segment_id = str(obs_by_id[observation_id].get("segment_id") or "")
            if not segment_id:
                continue
            conn.execute(
                """
                UPDATE segments
                SET deal_id = ?
                WHERE id = ? AND working_day_id = ? AND deal_id IS NULL
                """,
                (deal_id, segment_id, working_day_id),
            )
        inserted.append(
            {
                "id": deal_id,
                "title": cluster.title,
                "observation_ids": chosen,
                "index": group_key,
            }
        )
    return inserted


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
        grounded = _grounded_for_kind(item.claim_indexes, claims, "decision", text=item.text)
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
        grounded = _grounded_for_kind(item.claim_indexes, claims, "commitment", text=item.text)
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


_ACHIEVEMENT_KEY = re.compile(r"[^a-z0-9]+")


def _achievement_key(text: str) -> str:
    """Case/punctuation-folded identity for collapsing duplicate achievements."""
    return " ".join(_ACHIEVEMENT_KEY.sub(" ", text.casefold()).split())


def _project_bucket(conn: sqlcipher.Connection, raw_json: str) -> str | None:
    """First resolved project entity on the observation, else the NULL bucket."""
    try:
        payload = json.loads(raw_json)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    names = payload.get("project_candidates")
    if not isinstance(names, list):
        return None
    for name in names:
        if not isinstance(name, str):
            continue
        entity_id = _match_entity(conn, name, "project")
        if entity_id is not None:
            return entity_id
    return None


def _insert_achievements(
    conn: sqlcipher.Connection,
    working_day_id: str,
    *,
    pipeline_version: int,
    stamp: str,
) -> None:
    """Observed outcomes/achievements with change evidence. Never inferred-only.

    Duplicate texts on the same day, pipeline version, and project (or the
    NULL bucket when no project resolves) become one candidate that keeps
    every supporting claim (and that claim's evidence) as links.
    """
    rows = conn.execute(
        """
        SELECT c.id, c.text, o.id AS observation_id, o.raw_json
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
    groups: dict[tuple[str | None, str], list[Any]] = {}
    order: list[tuple[str | None, str]] = []
    buckets: dict[str, str | None] = {}
    skipped = rejected_claim_ids(
        conn, working_day_id=working_day_id, pipeline_version=pipeline_version
    )
    for row in rows:
        if str(row["id"]) in skipped:
            continue
        folded = _achievement_key(str(row["text"]))
        if not folded:
            continue
        observation_id = str(row["observation_id"])
        if observation_id not in buckets:
            buckets[observation_id] = _project_bucket(conn, str(row["raw_json"]))
        key = (buckets[observation_id], folded)
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(row)
    for key in order:
        members = groups[key]
        first = members[0]
        candidate_id = new_id()
        conn.execute(
            """
            INSERT INTO achievement_candidates (
                id, created_at, updated_at, working_day_id, claim_id, text, pipeline_version
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                candidate_id,
                stamp,
                stamp,
                working_day_id,
                str(first["id"]),
                str(first["text"]),
                pipeline_version,
            ),
        )
        for member in members:
            conn.execute(
                """
                INSERT INTO achievement_candidate_claims (
                    id, created_at, updated_at, candidate_id, claim_id
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (new_id(), stamp, stamp, candidate_id, str(member["id"])),
            )


def _insert_summary(
    conn: sqlcipher.Connection,
    working_day_id: str,
    verified: VerifiedNarrative,
    *,
    prompt_version: str,
    pipeline_version: int,
    stamp: str,
    extra_sections: dict[str, Any] | None = None,
) -> None:
    sections = verified.sections_json()
    if extra_sections:
        sections.update(extra_sections)
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
            json.dumps(sections),
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


def _reread_summary(
    conn: sqlcipher.Connection, working_day_id: str, pipeline_version: int
) -> dict[str, Any] | None:
    """Re-read the daily summary after checkpoint. None if the row is gone."""
    row = conn.execute(
        """
        SELECT id, working_day_id, pipeline_version, narrative_md
        FROM daily_summaries
        WHERE working_day_id = ? AND pipeline_version = ?
        """,
        (working_day_id, pipeline_version),
    ).fetchone()
    if row is None:
        return None
    return {
        "id": str(row["id"]),
        "working_day_id": str(row["working_day_id"]),
        "pipeline_version": int(row["pipeline_version"]),
        "narrative_md": str(row["narrative_md"]),
    }


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
    summary = _reread_summary(conn, working_day_id, pipeline_version)
    if expected.get("summaries", 0) and summary is None:
        raise ConsolidationError("summary missing after wal_checkpoint")
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
            "deals": counts["deals"],
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
        SELECT o.id, o.description, o.segment_id, o.raw_json
        FROM observations o
        JOIN segments s ON s.id = o.segment_id
        WHERE s.working_day_id = ? AND o.pipeline_version = ?
        ORDER BY s.started_at, o.id
        """,
        (working_day_id, pipeline_version),
    ).fetchall()
    return [
        {
            "id": str(row["id"]),
            "description": str(row["description"]),
            "segment_id": str(row["segment_id"]),
            "raw_json": str(row["raw_json"] or ""),
        }
        for row in rows
    ]


def _load_claims(
    conn: sqlcipher.Connection, working_day_id: str, pipeline_version: int
) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT id, observation_id, working_day_id, kind, assertion, text, pipeline_version
        FROM claims
        WHERE working_day_id = ? AND pipeline_version = ?
        ORDER BY created_at, kind, assertion, text, id
        """,
        (working_day_id, pipeline_version),
    ).fetchall()
    return [
        {
            "id": str(row["id"]),
            "observation_id": str(row["observation_id"]),
            "working_day_id": str(row["working_day_id"]),
            "kind": str(row["kind"]),
            "assertion": str(row["assertion"]),
            "text": str(row["text"]),
            "pipeline_version": int(row["pipeline_version"]),
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


def _reset_job(
    conn: sqlcipher.Connection,
    job_id: str,
    *,
    now: datetime | None,
    preserve_attempts: bool = False,
) -> None:
    stamp = iso_now(now)
    if preserve_attempts:
        conn.execute(
            """
            UPDATE jobs
            SET state = ?, last_error = NULL, claimed_by = NULL,
                leased_until = NULL, run_after = ?, updated_at = ?
            WHERE id = ?
            """,
            (JobState.PENDING.value, stamp, stamp, job_id),
        )
    else:
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


def understand_block_reason(
    conn: sqlcipher.Connection,
    working_day_id: str,
    *,
    pipeline_version: int = PIPELINE_VERSION,
    close_after_seconds: int = DEFAULT_SEGMENT_CLOSE_AFTER_SECONDS,
    now: datetime | None = None,
) -> str | None:
    """``wait`` / ``None``. Shared by consolidation hold and eval drain."""
    recover_expired_leases(conn, now=now)
    return _understand_incomplete(
        conn,
        working_day_id,
        pipeline_version=pipeline_version,
        close_after_seconds=close_after_seconds,
    )


def _understand_has_claimed(
    conn: sqlcipher.Connection, working_day_id: str, *, pipeline_version: int
) -> bool:
    """True when a prior worker still holds an understand lease for this day."""
    return any(
        job.state is JobState.CLAIMED
        for job in _understand_jobs_for_day(conn, working_day_id, pipeline_version)
    )


def _understand_incomplete(
    conn: sqlcipher.Connection,
    working_day_id: str,
    *,
    pipeline_version: int,
    close_after_seconds: int,
) -> str | None:
    """``wait`` / ``None`` while understand work for this day is unfinished.

    Claimed, pending, or chat-done-but-unresolved jobs defer consolidation
    (``ConsolidationWait``) so a SIGKILL inside the lease cannot mark the
    day consolidated with missing entities. Terminal-failed jobs are skipped
    and flagged; they do not block the day.
    """
    states = {job.state for job in _understand_jobs_for_day(conn, working_day_id, pipeline_version)}
    if JobState.CLAIMED in states or JobState.PENDING in states:
        return "wait"
    if _understand_is_held(
        conn,
        working_day_id,
        pipeline_version=pipeline_version,
        close_after_seconds=close_after_seconds,
    ):
        return "wait"
    return None


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


def skipped_understand_segment_ids(
    conn: sqlcipher.Connection,
    working_day_id: str,
    *,
    pipeline_version: int = PIPELINE_VERSION,
    close_after_seconds: int = DEFAULT_SEGMENT_CLOSE_AFTER_SECONDS,
) -> list[str]:
    """Failed understand jobs plus leftover eligible segments (no silent drop)."""
    failed = {
        str(job.payload.get("segment_id") or "")
        for job in _understand_jobs_for_day(conn, working_day_id, pipeline_version)
        if job.state is JobState.FAILED
    }
    leftover = set(
        eligible_segment_ids(
            conn,
            pipeline_version=pipeline_version,
            close_after_seconds=close_after_seconds,
            working_day_id=working_day_id,
        )
    )
    return sorted(item for item in (failed | leftover) if item)


def _record_understand_gaps(
    conn: sqlcipher.Connection,
    working_day_id: str,
    skipped: list[str],
    *,
    now: datetime | None,
) -> None:
    if not skipped:
        return
    stamp = iso_now(now)
    gap = f"understand_failed={len(skipped)}"
    row = conn.execute(
        "SELECT consolidation_gap FROM working_days WHERE id = ?", (working_day_id,)
    ).fetchone()
    existing = str(row[0]) if row is not None and row[0] else ""
    if _UNDERSTAND_FAILED_RE.search(existing):
        value = _UNDERSTAND_FAILED_RE.sub(gap, existing, count=1)
    elif existing:
        value = f"{existing},{gap}"
    else:
        value = gap
    conn.execute(
        "UPDATE working_days SET consolidation_gap = ?, updated_at = ? WHERE id = ?",
        (value, stamp, working_day_id),
    )
    conn.commit()


def _trim_numbered(items: list[str], *, item_max: int, list_max: int) -> str:
    lines: list[str] = []
    used = 0
    for index, raw in enumerate(items, start=1):
        text = " ".join(str(raw).split())
        if len(text) > item_max:
            text = text[: item_max - 1] + "…"
        line = f"{index}. {text}"
        extra = len(line) + (1 if lines else 0)
        if used + extra > list_max:
            break
        lines.append(line)
        used += extra
    return "\n".join(lines)


def _deal_created_at(stamp: str, index: int) -> str:
    """Stable per-deal created_at so UUID order cannot reshuffle same-second inserts."""
    if index <= 0:
        return stamp
    moment = parse_iso(stamp) + timedelta(seconds=index)
    return moment.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _is_none_deal_title(title: str) -> bool:
    """Unassigned cluster titles: strip, casefold, collapse space."""
    return " ".join(title.casefold().split()) in _NONE_DEAL_TITLES


def _day_roster(
    entities: list[dict[str, Any]],
    observations: list[dict[str, Any]],
    *,
    cap: int = _ROSTER_CAP,
) -> list[str]:
    """Person names for the summary prompt: day's people only, never projects."""
    day_people: set[str] = set()
    for row in observations:
        for name in _people_candidates_from_raw(str(row.get("raw_json") or "")):
            folded = " ".join(name.casefold().split())
            if folded:
                day_people.add(folded)
    names: list[str] = []
    seen: set[str] = set()
    for row in entities:
        if str(row.get("type") or "") != "person":
            continue
        name = str(row.get("canonical_name") or "").strip()
        folded = " ".join(name.casefold().split())
        if not name or folded in seen:
            continue
        if day_people and folded not in day_people:
            continue
        seen.add(folded)
        names.append(name)
        if len(names) >= cap:
            break
    return names


def _deal_group_key(index: int, title: str) -> int | str:
    """Identity for a deal cluster. Title is not unique across look-alike deals."""
    return index


def _people_candidates_from_raw(raw_json: str) -> list[str]:
    """Understanding people_candidates on the day's observations."""
    try:
        payload = json.loads(raw_json) if raw_json else {}
    except json.JSONDecodeError:
        return []
    if not isinstance(payload, dict):
        return []
    values = payload.get("people_candidates")
    if not isinstance(values, list):
        return []
    names: list[str] = []
    seen: set[str] = set()
    for item in values:
        if not isinstance(item, str):
            continue
        name = " ".join(item.split())
        folded = name.casefold()
        if not name or folded in seen:
            continue
        seen.add(folded)
        names.append(name)
    return names


def _deal_candidates_from_raw(raw_json: str) -> list[str]:
    """Understanding deal/project candidates feed clustering (D3)."""
    try:
        payload = json.loads(raw_json) if raw_json else {}
    except json.JSONDecodeError:
        return []
    if not isinstance(payload, dict):
        return []
    names: list[str] = []
    seen: set[str] = set()
    for key in ("deal_candidates", "project_candidates"):
        values = payload.get(key)
        if not isinstance(values, list):
            continue
        for item in values:
            if not isinstance(item, str):
                continue
            name = " ".join(item.split())
            folded = name.casefold()
            if not name or folded in seen:
                continue
            seen.add(folded)
            names.append(name)
    return names


def _observation_cluster_text(row: dict[str, Any]) -> str:
    description = str(row.get("description") or "")
    candidates = _deal_candidates_from_raw(str(row.get("raw_json") or ""))
    if not candidates:
        return description
    return f"{description} (deal candidates: {', '.join(candidates)})"


def _group_claims_by_deal(
    clustering: ClusteringOutput,
    observations: list[dict[str, Any]],
    claims: list[dict[str, Any]],
) -> list[tuple[object, str, list[dict[str, Any]]]]:
    obs_ids = [str(row["id"]) for row in observations]
    assigned: set[str] = set()
    groups: list[tuple[object, str, list[dict[str, Any]]]] = []
    for index, cluster in enumerate(clustering.deals):
        if _is_none_deal_title(cluster.title):
            continue
        chosen = _unique_observation_ids(cluster.observation_indexes, obs_ids, assigned)
        if not chosen:
            continue
        assigned.update(chosen)
        chosen_set = set(chosen)
        grouped = [row for row in claims if str(row.get("observation_id") or "") in chosen_set]
        if grouped:
            groups.append((_deal_group_key(index, cluster.title), cluster.title, grouped))
    return groups


def _append_missing_citations(text: str, claim_ids: Sequence[str], grouped_ids: set[str]) -> str:
    """Keep claim_ids-only model output by writing missing ``[c:id]`` into the text."""
    present = {tok for tok in CITATION_RE.findall(text) if tok in grouped_ids}
    missing = [cid for cid in claim_ids if cid in grouped_ids and cid not in present]
    if not missing:
        return text
    tokens = " ".join(f"[c:{cid}]" for cid in missing)
    stripped = text.rstrip()
    if stripped.endswith("."):
        return f"{stripped[:-1]} {tokens}."
    return f"{stripped} {tokens}".strip()


def _slot_from_prefix(text: str) -> str | None:
    """Infer a summary slot from a leading What happened / Who involved / Next step prefix."""
    head = str(text or "").lstrip().casefold()
    for prefix, slot in _SLOT_PREFIXES:
        if head.startswith(prefix):
            return slot
    return None


def _people_bearing(text: str) -> bool:
    folded = f" {text.casefold()} "
    if any(hint in folded for hint in _PEOPLE_HINTS):
        return True
    return bool(re.search(r"\b[A-Z][a-z]+\s+[A-Z][a-z]+\b", text))


def _slot_for_claim(row: dict[str, Any]) -> str:
    """Map one claim onto a summary slot without inventing facts.

    Prefer ``kind`` / ``assertion`` when they already name a slot. Otherwise
    ``person_interaction`` → who_involved, ``commitment`` → next_step, next-step
    language → next_step, people-bearing text → who_involved, remaining
    observed/work claims → what_happened.
    """
    kind = str(row.get("kind") or "").strip().casefold()
    assertion = str(row.get("assertion") or "").strip().casefold()
    if kind in _SUMMARY_SLOTS:
        return kind
    if assertion in _SUMMARY_SLOTS:
        return assertion
    if kind == "person_interaction":
        return "who_involved"
    if kind == "commitment":
        return "next_step"
    text = str(row.get("text") or "")
    folded = f" {text.casefold()} "
    if any(marker in folded for marker in _NEXT_STEP_MARKERS):
        return "next_step"
    if _people_bearing(text):
        return "who_involved"
    return "what_happened"


def _slot_drafts_from_claims(
    key: object, claims: list[dict[str, Any]]
) -> list[tuple[object, str, list[str]]]:
    """Per-deal fallback slots from claim rows. Ids are claim UUIDs, never indexes."""
    buckets: dict[str, list[str]] = {
        slot: [] for slot in ("what_happened", "who_involved", "next_step")
    }
    for row in claims:
        cid = str(row.get("id") or "")
        if not cid or cid.isdigit():
            continue
        buckets[_slot_for_claim(row)].append(cid)
    return [(key, slot, ids) for slot, ids in buckets.items() if ids]


def _bucket_deal_paragraphs(
    raw_summary: DailySummaryOutput,
    deal_groups: list[tuple[object, str, list[dict[str, Any]]]],
    *,
    working_day_id: str,
    pipeline_version: int,
    claims: list[dict[str, Any]],
) -> tuple[
    dict[object, list[str]],
    list[tuple[object, str, list[str]]],
    set[str],
    tuple[dict[str, str], ...],
]:
    """Assign each paragraph to one deal. Drop cross-deal and unmatched text."""
    buckets: dict[object, list[str]] = {key: [] for key, _title, _grouped in deal_groups}
    cited_by_key: dict[object, set[str]] = {key: set() for key, _title, _grouped in deal_groups}
    slot_drafts: list[tuple[object, str, list[str]]] = []
    if not deal_groups or not claims:
        return buckets, slot_drafts, set(), ()

    for para in raw_summary.paragraphs:
        owners: list[object] = []
        expanded_by_key: dict[object, dict[str, Any]] = {}
        payload = {"text": para.text, "claim_ids": list(para.claim_ids)}
        for key, _title, grouped in deal_groups:
            expanded = expand_index_citations([payload], grouped)[0]
            grouped_ids = {str(row["id"]) for row in grouped}
            mapped = [cid for cid in expanded["claim_ids"] if cid in grouped_ids]
            expanded["text"] = _append_missing_citations(str(expanded["text"]), mapped, grouped_ids)
            cited_toks = [
                tok for tok in CITATION_RE.findall(str(expanded["text"])) if tok in grouped_ids
            ]
            if cited_toks or mapped:
                owners.append(key)
                expanded_by_key[key] = expanded
        if len(owners) != 1:
            continue
        key = owners[0]
        grouped = next(rows for group_key, _title, rows in deal_groups if group_key == key)
        cite_claims = grouped
        verified = verify_narrative(
            [expanded_by_key[key]],
            cite_claims,
            working_day_id=working_day_id,
            pipeline_version=pipeline_version,
            fallback=False,
        )
        if not verified.paragraphs:
            continue
        buckets[key].extend(_strip_leading_headings(body) for body in verified.paragraphs)
        cited_by_key[key].update(verified.claim_ids)
        slot = para.slot or _slot_from_prefix(para.text)
        if slot:
            slot_drafts.append((key, slot, list(verified.claim_ids)))

    for key, _title, grouped in deal_groups:
        if buckets[key] or not grouped:
            continue
        cite_claims = grouped
        fallback = _cited_fallback(cite_claims)
        buckets[key].extend(_strip_leading_headings(body) for body in fallback.paragraphs)
        cited_by_key[key].update(fallback.claim_ids)
        slot_drafts.extend(_slot_drafts_from_claims(key, grouped))

    cited_ids: set[str] = set()
    unverified: list[dict[str, str]] = []
    for key, _title, grouped in deal_groups:
        cited_ids.update(cited_by_key[key])
        for row in grouped:
            if str(row["id"]) not in cited_by_key[key]:
                unverified.append(
                    {
                        "id": str(row["id"]),
                        "kind": str(row["kind"]),
                        "text": str(row["text"]),
                    }
                )
    return buckets, slot_drafts, cited_ids, tuple(unverified)


def _strip_leading_headings(text: str) -> str:
    """Drop model ``##`` lines so the stored deal title is the only heading."""
    lines = text.splitlines()
    while lines and lines[0].lstrip().startswith("##"):
        lines.pop(0)
        if lines and not lines[0].strip():
            lines.pop(0)
    return "\n".join(lines).strip()


def _deal_heading_block(title: str, paras: list[str]) -> str:
    return f"## {title}\n\n" + "\n\n".join(paras)


def _narrative_from_stored_deals(
    deals: list[dict[str, Any]],
    buckets: dict[object, list[str]],
    cited: set[str],
    unverified: tuple[dict[str, str], ...],
) -> VerifiedNarrative:
    """Headings come from the stored deal title, never leftover unmatched text."""
    parts: list[str] = []
    for deal in deals:
        paras = buckets.get(deal["index"], [])
        if not paras:
            continue
        parts.append(_deal_heading_block(str(deal["title"]), paras))
    body = "\n\n".join(parts)
    return VerifiedNarrative(
        narrative_md=body,
        paragraphs=tuple(parts),
        claim_ids=tuple(sorted(cited)),
        unverified=unverified,
    )


def _summary_deal_sections(
    deals: list[dict[str, Any]],
    slot_drafts: list[tuple[object, str, list[str]]],
    buckets: dict[object, list[str]] | None = None,
) -> dict[str, Any]:
    """Store validated slot citations and narrative blocks with deal_id."""
    key_to_id = {row["index"]: str(row["id"]) for row in deals}
    slots: list[dict[str, Any]] = []
    for key, slot, claim_ids in slot_drafts:
        deal_id = key_to_id.get(key)
        if not deal_id or not slot or not claim_ids:
            continue
        if any(not cid or cid.isdigit() for cid in claim_ids):
            continue
        slots.append({"slot": slot, "claim_ids": list(claim_ids), "deal_id": deal_id})
    payload: dict[str, Any] = {
        "deals": [{"id": row["id"], "title": row["title"]} for row in deals],
        "slots": slots,
    }
    if buckets is None:
        return payload
    paragraphs: list[dict[str, str]] = []
    for deal in deals:
        paras = buckets.get(deal["index"], [])
        if not paras:
            continue
        paragraphs.append(
            {
                "deal_id": str(deal["id"]),
                "text": _deal_heading_block(str(deal["title"]), paras),
            }
        )
    payload["paragraphs"] = paragraphs
    return payload


def _fallback_clustering(observations: list[dict[str, Any]]) -> ClusteringOutput:
    """Parked one-activity-per-observation; deals stay empty so leftover is ``none``."""
    activities: list[ActivityCluster] = []
    for index, row in enumerate(observations, start=1):
        description = " ".join(str(row.get("description") or "segment").split()) or "segment"
        activities.append(
            ActivityCluster(
                title=description[:80],
                summary=description[:200],
                observation_indexes=[index],
            )
        )
    return ClusteringOutput(activities=activities, deals=[])


def _fallback_summary(claims: list[dict[str, Any]]) -> DailySummaryOutput:
    if not claims:
        return DailySummaryOutput(paragraphs=[])
    cid = str(claims[0]["id"])
    return DailySummaryOutput(
        paragraphs=[
            SummaryParagraph(text=f"Work continued [c:{cid}].", claim_ids=[cid]),
        ]
    )


def _ground_tokens(text: str) -> set[str]:
    return {match.group(0) for match in _GROUND_TOKEN_RE.finditer(text.casefold())}


def _text_overlap(left: str, right: str) -> float:
    a, b = _ground_tokens(left), _ground_tokens(right)
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _grounded_for_kind(
    indexes: list[int],
    claims: list[dict[str, Any]],
    kind: str,
    *,
    text: str = "",
) -> list[str]:
    matching = [row for row in claims if str(row["kind"]) == kind]
    if not matching:
        return []
    matching_ids = {str(row["id"]) for row in matching}
    claim_ids = [str(row["id"]) for row in claims]
    cited = _pick_indexes(indexes, claim_ids)
    chosen = [cid for cid in cited if cid in matching_ids]
    if chosen:
        return chosen
    by_id = {str(row["id"]): row for row in claims}
    needles = [text, *[str(by_id[cid].get("text") or "") for cid in cited if cid in by_id]]
    needles = [blob for blob in needles if blob.strip()]
    if not needles:
        return []
    recovered: list[str] = []
    for row in matching:
        hay = str(row.get("text") or "")
        if any(_text_overlap(needle, hay) >= _GROUND_OVERLAP_MIN for needle in needles):
            recovered.append(str(row["id"]))
    return recovered


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


def _unique_observation_ids(
    indexes: list[int],
    obs_ids: list[str],
    assigned: set[str],
) -> list[str]:
    """First deal wins. Later copies of the same observation index are dropped."""
    picked = _pick_indexes(indexes, obs_ids)
    unique = [item for item in picked if item not in assigned]
    dropped_ids = [item for item in picked if item in assigned]
    if dropped_ids:
        dropped_indexes = [
            int(raw)
            for raw in indexes
            if 1 <= int(raw) <= len(obs_ids) and obs_ids[int(raw) - 1] in assigned
        ]
        log.warning(
            "duplicate observation index dropped",
            extra={
                "dropped_count": len(dropped_ids),
                "kept_count": len(unique),
                "dropped_ids": dropped_ids,
                "dropped_indexes": dropped_indexes,
            },
        )
    return unique


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
        return None, None, ConsolidateSettings()
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
    raise RuntimeUnavailable("intelligence runtime is unavailable")


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
    moment = _aware(now)
    stamp = iso_now(moment)
    run_after = iso_now(moment + timedelta(seconds=backoff_seconds(max(job.attempts, 1))))
    conn.execute(
        """
        UPDATE jobs
        SET state = ?, last_error = ?, claimed_by = NULL, leased_until = NULL,
            run_after = ?, updated_at = ?
        WHERE id = ? AND claimed_by = ?
        """,
        (JobState.PENDING.value, type(exc).__name__, run_after, stamp, job.id, worker_id),
    )
    conn.commit()


def _aware(now: datetime | None) -> datetime:
    moment = now or datetime.now(UTC)
    if moment.tzinfo is None:
        return moment.replace(tzinfo=UTC)
    return moment.astimezone(moment.tzinfo)
