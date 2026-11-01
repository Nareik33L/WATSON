"""Bounded extract-backlog drain. Capture/ingest never waits on this."""

from __future__ import annotations

import logging
from pathlib import Path

from sqlcipher3 import dbapi2 as sqlcipher

from watson.config.schema import DEFAULT_BACKLOG_BATCH_SIZE, DEFAULT_TIER2_GRACE_DAYS
from watson.pipeline.dedup import DEFAULT_DEDUP_THRESHOLD
from watson.pipeline.frames import EXTRACT_JOB_KIND, extract_fixture_stub
from watson.pipeline.jobs import JobLeaseError, claim, complete, fail

log = logging.getLogger("watson.pipeline.backlog")

WORKER_ID = "watson-worker"


def backlog_depth(conn: sqlcipher.Connection) -> int:
    """Queued extract work: pending + claimed extract jobs."""
    row = conn.execute(
        """
        SELECT COUNT(*) AS n FROM jobs
        WHERE kind = ? AND state IN ('pending', 'claimed')
        """,
        (EXTRACT_JOB_KIND,),
    ).fetchone()
    return int(row["n"]) if row is not None else 0


def drain_backlog(
    conn: sqlcipher.Connection,
    *,
    worker_id: str = WORKER_ID,
    limit: int = DEFAULT_BACKLOG_BATCH_SIZE,
    fixture_dir: Path | None = None,
    dedup_threshold: int = DEFAULT_DEDUP_THRESHOLD,
    tier2_grace_days: int = DEFAULT_TIER2_GRACE_DAYS,
) -> int:
    """Claim and complete at most ``limit`` extract jobs. Does not ingest.

    Returns the number of extract jobs completed on this call. Remaining
    queued frames stay pending so the next batch (or the next wake) can
    take them. Capture continues because ingest only enqueues.
    """
    if limit < 1:
        raise ValueError("backlog batch limit must be >= 1")
    completed = 0
    while completed < limit:
        job = claim(conn, worker_id=worker_id)
        if job is None:
            break
        if job.kind != EXTRACT_JOB_KIND:
            fail(conn, job.id, f"unexpected job kind {job.kind}", worker_id=worker_id)
            continue
        try:
            extract_fixture_stub(
                conn,
                job.payload,
                fixture_dir=_fixture_dir(job.payload, fixture_dir),
                dedup_threshold=dedup_threshold,
                tier2_grace_days=tier2_grace_days,
            )
            complete(conn, job.id, worker_id=worker_id)
            completed += 1
        except (JobLeaseError, OSError, ValueError, RuntimeError) as exc:
            fail(conn, job.id, type(exc).__name__, worker_id=worker_id)
            raise
    if completed:
        log.info("drained extract backlog", extra={"completed": completed, "limit": limit})
    return completed


def _fixture_dir(payload: dict[str, object], override: Path | None) -> Path:
    if override is not None:
        return override
    raw = payload.get("fixture_dir")
    if isinstance(raw, str) and raw:
        return Path(raw)
    sidecar = payload.get("sidecar_path")
    if isinstance(sidecar, str) and sidecar:
        # Replay jobs store the spool sidecar; the fixture root is unused
        # once the pair is already in the spool. A placeholder keeps the
        # extract stub's "never delete the fixture" check happy.
        return Path(sidecar).resolve().parent
    raise RuntimeError("extract job is missing fixture_dir")
