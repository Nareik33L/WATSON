"""Understanding worker: eligible segments → observation + claims (plan §3.5).

A segment is eligible once it is ``closed`` or dwell reaches
``close_after_seconds`` (default 20 minutes). Idempotent per
``(segment_id, pipeline_version)``. Every stored claim has at least one
``claim_evidence`` row (structural, §11.2).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlcipher3 import dbapi2 as sqlcipher

from watson.config.schema import (
    DEFAULT_ENTITY_SIMILARITY,
    DEFAULT_RETRIEVE_TOP_K,
    DEFAULT_SEGMENT_CLOSE_AFTER_SECONDS,
    IntelligenceConfig,
    WatsonConfig,
)
from watson.intelligence.prompts import load_prompt
from watson.intelligence.resolver import resolve_candidates
from watson.intelligence.retrieve import format_retrieved_context, retrieve_entity_context
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
    hold_understanding_jobs,
)
from watson.intelligence.runtime.fake import FakeRuntime
from watson.intelligence.schemas import UNDERSTANDING_JSON_SCHEMA, UnderstandingOutput
from watson.intelligence.structured import complete_structured
from watson.memory.db import iso_now
from watson.memory.ids import new_id
from watson.pipeline.frames import PIPELINE_VERSION
from watson.pipeline.jobs import Job, JobLeaseError, JobState, claim, complete, enqueue, fail

log = logging.getLogger("watson.pipeline.understanding")

WORKER_ID = "watson-understand"
PROMPT_TASK = "understanding"
PROMPT_VERSION = "v1"
MERGED_TEXT_PROMPT_MAX = 8000
SNIPPET_KIND_OCR = "ocr"


@dataclass(frozen=True)
class UnderstandSettings:
    pipeline_version: int = PIPELINE_VERSION
    close_after_seconds: int = DEFAULT_SEGMENT_CLOSE_AFTER_SECONDS
    retrieve_top_k: int = DEFAULT_RETRIEVE_TOP_K
    entity_similarity_threshold: float = DEFAULT_ENTITY_SIMILARITY
    merged_text_max_chars: int = MERGED_TEXT_PROMPT_MAX
    model_id: str = "fake"

    @classmethod
    def from_config(
        cls,
        config: WatsonConfig | IntelligenceConfig | None,
        *,
        model_id: str | None = None,
    ) -> UnderstandSettings:
        if isinstance(config, WatsonConfig):
            intel = config.intelligence
            close_after = int(config.segmentation.close_after_seconds)
        elif isinstance(config, IntelligenceConfig):
            intel = config
            close_after = DEFAULT_SEGMENT_CLOSE_AFTER_SECONDS
        else:
            return cls(model_id=model_id or "fake")
        return cls(
            close_after_seconds=close_after,
            retrieve_top_k=int(intel.retrieve_top_k),
            entity_similarity_threshold=float(intel.entity_similarity_threshold),
            model_id=model_id or intel.llm_model,
        )


def understand_idempotency_key(segment_id: str, pipeline_version: int = PIPELINE_VERSION) -> str:
    return f"understand:{segment_id}:{pipeline_version}"


def eligible_segment_ids(
    conn: sqlcipher.Connection,
    *,
    pipeline_version: int = PIPELINE_VERSION,
    close_after_seconds: int = DEFAULT_SEGMENT_CLOSE_AFTER_SECONDS,
    working_day_id: str | None = None,
) -> list[str]:
    """Closed, or still open after the 20-minute eligibility dwell."""
    day_sql = "AND working_day_id = ?" if working_day_id is not None else ""
    params: list[Any] = [close_after_seconds, pipeline_version]
    if working_day_id is not None:
        params.append(working_day_id)
    rows = conn.execute(
        f"""
        SELECT id FROM segments
        WHERE (state = 'closed' OR dwell_seconds >= ?)
          AND NOT EXISTS (
              SELECT 1 FROM observations
              WHERE observations.segment_id = segments.id
                AND observations.pipeline_version = ?
          )
          {day_sql}
        ORDER BY started_at, id
        """,
        params,
    ).fetchall()
    return [str(row["id"]) for row in rows]


def enqueue_eligible_understand_jobs(
    conn: sqlcipher.Connection,
    *,
    pipeline_version: int = PIPELINE_VERSION,
    close_after_seconds: int = DEFAULT_SEGMENT_CLOSE_AFTER_SECONDS,
    working_day_id: str | None = None,
    now: datetime | None = None,
) -> int:
    """Enqueue one ``understand`` job per eligible segment. Idempotent."""
    added = 0
    for segment_id in eligible_segment_ids(
        conn,
        pipeline_version=pipeline_version,
        close_after_seconds=close_after_seconds,
        working_day_id=working_day_id,
    ):
        key = understand_idempotency_key(segment_id, pipeline_version)
        before = conn.execute("SELECT id FROM jobs WHERE idempotency_key = ?", (key,)).fetchone()
        enqueue(
            conn,
            kind=UNDERSTAND_JOB_KIND,
            payload={"segment_id": segment_id, "pipeline_version": pipeline_version},
            idempotency_key=key,
            now=now,
        )
        if before is None:
            added += 1
    return added


def drain_understand_jobs(
    conn: sqlcipher.Connection,
    *,
    runtime: LanguageRuntime | None = None,
    embedding: EmbeddingRuntime | None = None,
    config: WatsonConfig | IntelligenceConfig | None = None,
    worker_id: str = WORKER_ID,
    limit: int = 10_000,
    now: datetime | None = None,
) -> int:
    """Claim and complete at most ``limit`` understand jobs.

    A missing runtime leaves jobs ``pending`` with a reason and returns 0.
    """
    if limit < 1:
        raise ValueError("understand batch limit must be >= 1")
    language, embedder, settings = _resolve_adapters(
        conn, runtime=runtime, embedding=embedding, config=config, now=now
    )
    if language is None or embedder is None:
        return 0
    completed = 0
    while completed < limit:
        job = claim(conn, worker_id=worker_id, kind=UNDERSTAND_JOB_KIND, now=now)
        if job is None:
            break
        try:
            understand_job(conn, job, language, embedder, settings=settings, now=now)
            complete(conn, job.id, worker_id=worker_id, now=now)
            completed += 1
        except JobLeaseError:
            raise
        except RuntimeUnavailable as exc:
            _hold_claimed_understand_job(conn, job, worker_id, exc, now=now)
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
        log.info("drained understand jobs", extra={"completed": completed, "limit": limit})
    return completed


def understand_job(
    conn: sqlcipher.Connection,
    job: Job,
    language: LanguageRuntime,
    embedding: EmbeddingRuntime,
    *,
    settings: UnderstandSettings | None = None,
    now: datetime | None = None,
) -> str | None:
    """Run one understand job. Returns the observation id, or None if skipped."""
    cfg = settings or UnderstandSettings()
    segment_id = str(job.payload.get("segment_id") or "")
    if not segment_id:
        raise JobFailure("understand job is missing segment_id", retryable=False)
    pipeline_version = int(job.payload.get("pipeline_version") or cfg.pipeline_version)
    return understand_segment(
        conn,
        segment_id,
        language,
        embedding,
        settings=UnderstandSettings(
            pipeline_version=pipeline_version,
            close_after_seconds=cfg.close_after_seconds,
            retrieve_top_k=cfg.retrieve_top_k,
            entity_similarity_threshold=cfg.entity_similarity_threshold,
            merged_text_max_chars=cfg.merged_text_max_chars,
            model_id=cfg.model_id,
        ),
        now=now,
    )


def understand_segment(
    conn: sqlcipher.Connection,
    segment_id: str,
    language: LanguageRuntime,
    embedding: EmbeddingRuntime,
    *,
    settings: UnderstandSettings | None = None,
    now: datetime | None = None,
) -> str | None:
    """Build input, call the LLM, persist observation/claims/evidence, resolve entities."""
    cfg = settings or UnderstandSettings()
    existing = conn.execute(
        "SELECT id FROM observations WHERE segment_id = ? AND pipeline_version = ?",
        (segment_id, cfg.pipeline_version),
    ).fetchone()
    if existing is not None:
        return str(existing["id"])

    segment = _load_segment(conn, segment_id)
    if segment is None:
        raise JobFailure(f"understand job refers to missing segment {segment_id}", retryable=False)

    prompt = load_prompt(PROMPT_TASK, PROMPT_VERSION)
    memory = retrieve_entity_context(conn, str(segment["merged_text"]), top_k=cfg.retrieve_top_k)
    user = build_understand_user_message(
        segment, memory_block=format_retrieved_context(memory), max_chars=cfg.merged_text_max_chars
    )
    messages: list[Message] = [
        {"role": "system", "content": prompt.text},
        {"role": "user", "content": user},
    ]
    try:
        output = complete_structured(
            language, messages, UnderstandingOutput, schema=UNDERSTANDING_JSON_SCHEMA
        )
    except StructuredOutputError:
        raise
    except RuntimeUnavailable:
        raise
    except JobFailure:
        raise
    except Exception as exc:
        raise JobFailure("understanding complete failed", retryable=True) from exc

    stamp = iso_now(now)
    try:
        observation_id = _persist_observation(
            conn,
            segment,
            output,
            prompt_version=prompt.prompt_version,
            pipeline_version=cfg.pipeline_version,
            model_id=cfg.model_id,
            stamp=stamp,
        )
        resolve_candidates(
            conn,
            embedding,
            project_candidates=output.project_candidates,
            people_candidates=output.people_candidates,
            threshold=cfg.entity_similarity_threshold,
            seen_at=str(segment["ended_at"]),
        )
        conn.commit()
    except sqlcipher.IntegrityError:
        conn.rollback()
        row = conn.execute(
            "SELECT id FROM observations WHERE segment_id = ? AND pipeline_version = ?",
            (segment_id, cfg.pipeline_version),
        ).fetchone()
        if row is None:
            raise
        return str(row["id"])
    except Exception:
        conn.rollback()
        raise
    return observation_id


def build_understand_user_message(
    segment: dict[str, Any],
    *,
    memory_block: str,
    max_chars: int = MERGED_TEXT_PROMPT_MAX,
) -> str:
    numbered = number_merged_text(str(segment.get("merged_text") or ""), max_chars=max_chars)
    url = segment.get("url_domain") or "(none)"
    app = segment.get("app_name") or segment.get("bundle_id") or "(unknown)"
    return (
        f"App: {app}\n"
        f"Window: {segment.get('window_title_norm') or '(none)'}\n"
        f"URL: {url}\n"
        f"Dwell: {int(segment.get('dwell_seconds') or 0)}s\n"
        f"Frames: {int(segment.get('frame_count') or 0)}\n"
        f"Line changes: {int(segment.get('line_changes') or 0)}\n"
        f"\nMerged text:\n{numbered}\n"
        f"\nRetrieved context:\n{memory_block}\n"
    )


def number_merged_text(text: str, *, max_chars: int) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    numbered = [f"{index}. {line}" for index, line in enumerate(lines, start=1)]
    if not numbered:
        return "(empty)"
    out: list[str] = []
    used = 0
    for line in numbered:
        extra = len(line) + (1 if out else 0)
        if used + extra > max_chars:
            break
        out.append(line)
        used += extra
    return "\n".join(out) if out else numbered[0][:max_chars]


def _resolve_adapters(
    conn: sqlcipher.Connection,
    *,
    runtime: LanguageRuntime | None,
    embedding: EmbeddingRuntime | None,
    config: WatsonConfig | IntelligenceConfig | None,
    now: datetime | None,
) -> tuple[LanguageRuntime | None, EmbeddingRuntime | None, UnderstandSettings]:
    if runtime is not None:
        embedder: EmbeddingRuntime = embedding if embedding is not None else _as_embedder(runtime)
        model_id = getattr(runtime, "name", None)
        if isinstance(model_id, str) and model_id:
            settings = UnderstandSettings.from_config(config, model_id=model_id)
        else:
            settings = UnderstandSettings.from_config(config)
        return runtime, embedder, settings
    if config is None:
        config = IntelligenceConfig(runtime="fake")
    held = hold_if_unavailable(conn, config, now=now)
    if held.held:
        return None, None, UnderstandSettings.from_config(config)
    bundle: RuntimeBundle = build_runtime(config)
    return (
        bundle.language,
        bundle.embedding,
        UnderstandSettings.from_config(config, model_id=bundle.settings.llm_model),
    )


def _as_embedder(runtime: LanguageRuntime) -> EmbeddingRuntime:
    embed = getattr(runtime, "embed", None)
    if callable(embed):
        return runtime  # type: ignore[return-value]
    return FakeRuntime()


def _load_segment(conn: sqlcipher.Connection, segment_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT
            s.id, s.working_day_id, s.bundle_id, s.window_title_norm, s.url_domain,
            s.started_at, s.ended_at, s.frame_count, s.dwell_seconds, s.line_changes,
            s.merged_text, s.state, s.start_frame_id, f.app_name
        FROM segments s
        LEFT JOIN frames f ON f.id = s.start_frame_id
        WHERE s.id = ?
        """,
        (segment_id,),
    ).fetchone()
    if row is None:
        return None
    return {key: row[key] for key in row.keys()}


def _persist_observation(
    conn: sqlcipher.Connection,
    segment: dict[str, Any],
    output: UnderstandingOutput,
    *,
    prompt_version: str,
    pipeline_version: int,
    model_id: str,
    stamp: str,
) -> str:
    observation_id = new_id()
    raw = output.model_dump(mode="json")
    conn.execute(
        """
        INSERT INTO observations (
            id, created_at, updated_at, segment_id, activity_type, description,
            confidence, model_id, prompt_version, pipeline_version, raw_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            observation_id,
            stamp,
            stamp,
            segment["id"],
            output.activity_type.value,
            output.description,
            float(output.confidence),
            model_id,
            prompt_version,
            pipeline_version,
            json.dumps(raw, sort_keys=True),
        ),
    )
    raw_merged = str(segment.get("merged_text") or "")
    lines = [line.strip() for line in raw_merged.splitlines() if line.strip()]
    for candidate in output.claims:
        if not candidate.evidence:
            raise JobFailure("understanding produced a claim without evidence", retryable=False)
        supported = [item for item in candidate.evidence if _citation_supported(item, lines)]
        claim_id = new_id()
        conn.execute(
            """
            INSERT INTO claims (
                id, created_at, updated_at, observation_id, working_day_id, kind, text,
                assertion, confidence, status, due_hint, pipeline_version
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, ?)
            """,
            (
                claim_id,
                stamp,
                stamp,
                observation_id,
                segment["working_day_id"],
                candidate.kind.value,
                candidate.text,
                candidate.assertion.value,
                float(output.confidence),
                pipeline_version,
            ),
        )
        for citation in supported:
            snippet = citation.snippet.strip()[:300]
            frame_id = _frame_for_snippet(conn, str(segment["id"]), snippet)
            conn.execute(
                """
                INSERT INTO claim_evidence (
                    id, created_at, updated_at, claim_id, segment_id, frame_id,
                    snippet, snippet_kind, line_start, line_end
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    new_id(),
                    stamp,
                    stamp,
                    claim_id,
                    segment["id"],
                    frame_id,
                    snippet,
                    SNIPPET_KIND_OCR,
                    int(citation.line_start),
                    int(citation.line_end),
                ),
            )
    orphan = conn.execute(
        """
        SELECT c.id FROM claims c
        LEFT JOIN claim_evidence e ON e.claim_id = c.id
        WHERE c.observation_id = ? AND e.id IS NULL
        """,
        (observation_id,),
    ).fetchone()
    if orphan is not None:
        raise JobFailure("stored claim is missing claim_evidence", retryable=False)
    return observation_id


def _citation_supported(citation: Any, lines: list[str]) -> bool:
    """Keep citations whose 1-based line span exists in merged text."""
    snippet = str(getattr(citation, "snippet", "") or "").strip()
    start = int(getattr(citation, "line_start", 0) or 0)
    end = int(getattr(citation, "line_end", 0) or 0)
    return bool(snippet) and bool(lines) and 1 <= start <= end <= len(lines)


def _hold_claimed_understand_job(
    conn: sqlcipher.Connection,
    job: Job,
    worker_id: str,
    exc: RuntimeUnavailable,
    *,
    now: datetime | None,
) -> None:
    """§7.4: return the lease and keep the job pending without burning attempts."""
    stamp = iso_now(now)
    reason = type(exc).__name__
    conn.execute(
        """
        UPDATE jobs
        SET state = ?, last_error = ?, claimed_by = NULL, leased_until = NULL, updated_at = ?
        WHERE id = ? AND claimed_by = ?
        """,
        (JobState.PENDING.value, reason, stamp, job.id, worker_id),
    )
    conn.commit()
    hold_understanding_jobs(conn, reason, now=now)


def _frame_for_snippet(conn: sqlcipher.Connection, segment_id: str, snippet: str) -> str | None:
    row = conn.execute(
        """
        SELECT t.frame_id
        FROM segment_frames sf
        JOIN frame_text t ON t.frame_id = sf.frame_id
        WHERE sf.segment_id = ? AND instr(t.text_redacted, ?) > 0
        ORDER BY sf.captured_at, sf.frame_id
        LIMIT 1
        """,
        (segment_id, snippet),
    ).fetchone()
    return None if row is None else str(row["frame_id"])
