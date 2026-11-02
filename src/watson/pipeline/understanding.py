"""Understanding worker: eligible segments → observation + claims (plan §3.5).

A segment is eligible once it is ``closed`` or dwell reaches
``close_after_seconds`` (default 20 minutes). Idempotent per
``(segment_id, pipeline_version)``. Every stored claim has at least one
``claim_evidence`` row (structural, §11.2).
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from pydantic import ValidationError
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
    end_runtime_phase,
    format_job_error,
    hold_if_unavailable,
    hold_understanding_jobs,
)
from watson.intelligence.runtime.fake import FakeRuntime
from watson.intelligence.runtime.stats import record_understand_prompt, record_understand_segment
from watson.intelligence.schemas import UNDERSTANDING_JSON_SCHEMA, UnderstandingOutput
from watson.intelligence.schemas.understanding import Assertion, ClaimKind
from watson.intelligence.structured import complete_structured
from watson.memory.db import iso_now
from watson.memory.ids import new_id
from watson.pipeline.frames import PIPELINE_VERSION
from watson.pipeline.jobs import (
    DEFAULT_LEASE_SECONDS,
    DEFAULT_MAX_ATTEMPTS,
    Job,
    JobLeaseError,
    JobState,
    claim,
    complete,
    enqueue,
    fail,
)
from watson.privacy.logging import ContentField

log = logging.getLogger("watson.pipeline.understanding")

WORKER_ID = "watson-understand"
PROMPT_TASK = "understanding"
PROMPT_VERSION = "v3"
# Chat + deferred embed for a day's segments can exceed the default 60s lease.
UNDERSTAND_LEASE_SECONDS = max(DEFAULT_LEASE_SECONDS, 3600)
# Numbered merged OCR in the understanding user message. 3000 characters is
# ~750 tokens; with the v3 system prompt, retrieved aliases, JSON schema,
# and num_predict=512 this fits the default 4096-token Ollama context.
MERGED_TEXT_PROMPT_MAX = 3000
SNIPPET_KIND_OCR = "ocr"
_COMPLETION_RE = re.compile(
    r"\b(done|sent|approved|merged|published|shipped|completed|"
    r"submitted|deployed|signed[- ]off|checked(?:\s+off)?)\b",
    re.IGNORECASE,
)
_INTENTION_RE = re.compile(
    r"\b(will|please|todo|need to|going to|i['’]ll|i will)\b",
    re.IGNORECASE,
)


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
            merged_text_max_chars=MERGED_TEXT_PROMPT_MAX,
            model_id=model_id or intel.llm_model,
        )


@dataclass(frozen=True)
class _PendingResolve:
    """Chat persisted; resolver embeddings run after the LLM is unloaded."""

    output: UnderstandingOutput
    segment: dict[str, Any]
    settings: UnderstandSettings


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

    Chat completions run first with only the LLM loaded. Resolver embeddings
    are a deferred batch after that sub-phase unloads (§11.4). A job is
    marked completed only after that batch commits. Chat-done work stays
    claimed until then; a caught crash parks it ``pending`` so a rerun
    resolves from ``observations.raw_json`` without calling the LLM.
    ``SIGKILL`` leaves jobs ``claimed`` for the lease; consolidation must
    not mark that day consolidated while any of those jobs are claimed,
    pending, or unresolved. Poison ``raw_json`` fails that job and the
    drain continues. A missing runtime leaves jobs ``pending`` with a
    reason and returns 0.
    """
    if limit < 1:
        raise ValueError("understand batch limit must be >= 1")
    language, embedder, settings = _resolve_adapters(
        conn, runtime=runtime, embedding=embedding, config=config, now=now
    )
    if language is None or embedder is None:
        return 0
    completed = 0
    pending: list[tuple[str, UnderstandSettings]] = []
    held: list[Job] = []
    try:
        while len(held) < limit:
            job = claim(
                conn,
                worker_id=worker_id,
                kind=UNDERSTAND_JOB_KIND,
                lease_seconds=UNDERSTAND_LEASE_SECONDS,
                now=now,
            )
            if job is None:
                break
            try:
                work = understand_job(
                    conn, job, language, embedder, settings=settings, now=now, defer_resolve=True
                )
                if isinstance(work, _PendingResolve):
                    pending.append((str(work.segment["id"]), work.settings))
                held.append(job)
            except JobLeaseError:
                raise
            except RuntimeUnavailable as exc:
                _hold_claimed_understand_job(conn, job, worker_id, exc, now=now)
                break
            except JobFailure as exc:
                fail(
                    conn,
                    job.id,
                    format_job_error(exc),
                    worker_id=worker_id,
                    now=now,
                    max_attempts=1 if not exc.retryable else DEFAULT_MAX_ATTEMPTS,
                )
                if not exc.retryable:
                    continue
                break
            except (OSError, ValueError, RuntimeError, sqlcipher.DatabaseError) as exc:
                fail(conn, job.id, format_job_error(exc), worker_id=worker_id, now=now)
                raise
        if pending:
            # Chat sub-phase done. Unload the LLM before any resolver embeddings.
            # Reload from raw_json so the day's outputs are not held in RAM.
            _end_phase(language, embedder)
            for segment_id, job_settings in pending:
                _resolve_stored_observation(conn, embedder, segment_id, job_settings)
            conn.commit()
        remaining = list(held)
        for job in remaining:
            complete(conn, job.id, worker_id=worker_id, now=now)
            held.remove(job)
            completed += 1
    except Exception:
        try:
            conn.rollback()
        except sqlcipher.DatabaseError:
            pass
        for job in held:
            _park_claimed_understand_job(conn, job, worker_id, now=now)
        raise
    finally:
        _end_phase(language, embedder)
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
    defer_resolve: bool = False,
) -> str | _PendingResolve | None:
    """Run one understand job. Returns pending resolver work when chat persisted."""
    cfg = settings or UnderstandSettings()
    segment_id = str(job.payload.get("segment_id") or "")
    if not segment_id:
        raise JobFailure("understand job is missing segment_id", retryable=False)
    pipeline_version = int(job.payload.get("pipeline_version") or cfg.pipeline_version)
    job_settings = UnderstandSettings(
        pipeline_version=pipeline_version,
        close_after_seconds=cfg.close_after_seconds,
        retrieve_top_k=cfg.retrieve_top_k,
        entity_similarity_threshold=cfg.entity_similarity_threshold,
        merged_text_max_chars=cfg.merged_text_max_chars,
        model_id=cfg.model_id,
    )
    return understand_segment(
        conn,
        segment_id,
        language,
        embedding,
        settings=job_settings,
        now=now,
        defer_resolve=defer_resolve,
    )


def understand_segment(
    conn: sqlcipher.Connection,
    segment_id: str,
    language: LanguageRuntime,
    embedding: EmbeddingRuntime,
    *,
    settings: UnderstandSettings | None = None,
    now: datetime | None = None,
    defer_resolve: bool = False,
) -> str | _PendingResolve | None:
    """Build input, call the LLM, persist observation/claims/evidence, resolve entities."""
    cfg = settings or UnderstandSettings()
    existing = conn.execute(
        "SELECT id, raw_json FROM observations WHERE segment_id = ? AND pipeline_version = ?",
        (segment_id, cfg.pipeline_version),
    ).fetchone()
    if existing is not None:
        if defer_resolve:
            return _pending_from_stored(conn, segment_id, str(existing["raw_json"]), cfg=cfg)
        return str(existing["id"])

    started = time.perf_counter()
    try:
        return _understand_segment_body(
            conn,
            segment_id,
            language,
            embedding,
            cfg=cfg,
            now=now,
            defer_resolve=defer_resolve,
        )
    finally:
        record_understand_segment(time.perf_counter() - started)


def _understand_segment_body(
    conn: sqlcipher.Connection,
    segment_id: str,
    language: LanguageRuntime,
    embedding: EmbeddingRuntime,
    *,
    cfg: UnderstandSettings,
    now: datetime | None,
    defer_resolve: bool,
) -> str | _PendingResolve | None:
    segment = _load_segment(conn, segment_id)
    if segment is None:
        raise JobFailure(f"understand job refers to missing segment {segment_id}", retryable=False)

    log.info(
        "understand segment",
        extra={"merged_text": ContentField(str(segment.get("merged_text") or ""))},
    )
    prompt = load_prompt(PROMPT_TASK, PROMPT_VERSION)
    memory = retrieve_entity_context(conn, str(segment["merged_text"]), top_k=cfg.retrieve_top_k)
    user = build_understand_user_message(
        segment, memory_block=format_retrieved_context(memory), max_chars=cfg.merged_text_max_chars
    )
    record_understand_prompt(len(user))
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
        conn.commit()
    except sqlcipher.IntegrityError:
        conn.rollback()
        row = conn.execute(
            "SELECT id, raw_json FROM observations WHERE segment_id = ? AND pipeline_version = ?",
            (segment_id, cfg.pipeline_version),
        ).fetchone()
        if row is None:
            raise
        if defer_resolve:
            return _pending_from_stored(conn, segment_id, str(row["raw_json"]), cfg=cfg)
        return str(row["id"])
    except Exception:
        conn.rollback()
        raise

    if defer_resolve:
        return _PendingResolve(output=output, segment=segment, settings=cfg)

    try:
        _end_phase(language, embedding)
        resolve_candidates(
            conn,
            embedding,
            project_candidates=output.project_candidates,
            people_candidates=output.people_candidates,
            threshold=cfg.entity_similarity_threshold,
            seen_at=str(segment["ended_at"]),
        )
        conn.commit()
        return observation_id
    except Exception:
        conn.rollback()
        raise
    finally:
        _end_phase(language, embedding)


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
        f"\nMerged text (capped at {max_chars} characters):\n{numbered}\n"
        f"\nRetrieved context:\n{memory_block}\n"
    )


def number_merged_text(text: str, *, max_chars: int) -> str:
    """Number non-empty OCR lines and stop before ``max_chars`` (default 3000)."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    numbered = [f"{index}. {line}" for index, line in enumerate(lines, start=1)]
    if not numbered:
        return "(empty)"
    out: list[str] = []
    used = 0
    truncated = False
    for line in numbered:
        extra = len(line) + (1 if out else 0)
        if used + extra > max_chars:
            truncated = True
            break
        out.append(line)
        used += extra
    if not out:
        body = numbered[0][:max_chars]
        truncated = len(numbered[0]) > max_chars or len(numbered) > 1
    else:
        body = "\n".join(out)
    if truncated:
        return f"{body}\n(truncated)"
    return body


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


def _end_phase(language: LanguageRuntime, embedder: EmbeddingRuntime) -> None:
    """Unload at a sub-phase boundary. Same object is only ended once."""
    end_runtime_phase(language)
    if id(embedder) != id(language):
        end_runtime_phase(embedder)


def _resolve_stored_observation(
    conn: sqlcipher.Connection,
    embedder: EmbeddingRuntime,
    segment_id: str,
    cfg: UnderstandSettings,
) -> None:
    row = conn.execute(
        "SELECT raw_json FROM observations WHERE segment_id = ? AND pipeline_version = ?",
        (segment_id, cfg.pipeline_version),
    ).fetchone()
    if row is None:
        raise JobFailure(
            f"understand job refers to missing observation for {segment_id}", retryable=False
        )
    item = _pending_from_stored(conn, segment_id, str(row["raw_json"]), cfg=cfg)
    resolve_candidates(
        conn,
        embedder,
        project_candidates=item.output.project_candidates,
        people_candidates=item.output.people_candidates,
        threshold=item.settings.entity_similarity_threshold,
        seen_at=str(item.segment["ended_at"]),
    )


def _pending_from_stored(
    conn: sqlcipher.Connection,
    segment_id: str,
    raw_json: str,
    *,
    cfg: UnderstandSettings,
) -> _PendingResolve:
    """Rebuild resolver work from a persisted observation. No chat call."""
    segment = _load_segment(conn, segment_id)
    if segment is None:
        raise JobFailure(f"understand job refers to missing segment {segment_id}", retryable=False)
    try:
        payload = json.loads(raw_json)
        output = UnderstandingOutput.model_validate(payload)
    except (ValueError, TypeError, ValidationError) as exc:
        log.warning(
            "poison observation raw_json",
            extra={"segment_id": segment_id, "error": type(exc).__name__},
        )
        raise JobFailure("observation raw_json is invalid", retryable=False) from exc
    return _PendingResolve(output=output, segment=segment, settings=cfg)


def _park_claimed_understand_job(
    conn: sqlcipher.Connection,
    job: Job,
    worker_id: str,
    *,
    now: datetime | None,
) -> None:
    """Return a chat-done claim to pending without burning attempts or completing."""
    stamp = iso_now(now)
    conn.execute(
        """
        UPDATE jobs
        SET state = ?, claimed_by = NULL, leased_until = NULL, updated_at = ?
        WHERE id = ? AND claimed_by = ? AND state = ?
        """,
        (JobState.PENDING.value, stamp, job.id, worker_id, JobState.CLAIMED.value),
    )
    conn.commit()


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
        supported = [item for item in candidate.evidence if _citation_supported(item, lines)]
        if not supported:
            continue
        kind = candidate.kind
        assertion = candidate.assertion
        if (
            kind in {ClaimKind.OUTCOME, ClaimKind.ACHIEVEMENT}
            and assertion is Assertion.OBSERVED
            and not any(has_completion_evidence(item.snippet) for item in supported)
        ):
            assertion = Assertion.UNCONFIRMED
        if (
            kind in {ClaimKind.DECISION, ClaimKind.COMMITMENT}
            and assertion is Assertion.UNCONFIRMED
        ):
            continue
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
                kind.value,
                candidate.text,
                assertion.value,
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


def has_completion_evidence(text: str) -> bool:
    """True when OCR text records a finished action, not an intention."""
    blob = text.strip()
    if not blob or not _COMPLETION_RE.search(blob):
        return False
    if _INTENTION_RE.search(blob) and not re.search(
        r"\b(sent|approved|merged|published|shipped|completed|submitted|deployed|done)\b",
        blob,
        re.IGNORECASE,
    ):
        return False
    return True


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
    reason = format_job_error(exc)
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
