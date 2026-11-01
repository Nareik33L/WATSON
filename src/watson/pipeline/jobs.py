"""Durable job queue coordinated through SQLite (plan D5)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any

from sqlcipher3 import dbapi2 as sqlcipher

from watson.memory.db import iso_now, parse_iso, row_to_dict
from watson.memory.ids import new_id

DEFAULT_LEASE_SECONDS = 60
DEFAULT_MAX_ATTEMPTS = 5
MAX_BACKOFF_SECONDS = 300


class JobState(StrEnum):
    PENDING = "pending"
    CLAIMED = "claimed"
    COMPLETED = "completed"
    FAILED = "failed"


class JobLeaseError(RuntimeError):
    """complete/fail did not match an active lease owned by this worker."""


@dataclass(frozen=True)
class Job:
    id: str
    kind: str
    payload: dict[str, Any]
    idempotency_key: str
    state: JobState
    attempts: int
    run_after: str
    last_error: str | None
    leased_until: str | None
    claimed_by: str | None
    created_at: str
    updated_at: str

    @classmethod
    def from_row(cls, row: Any) -> Job:
        data = row_to_dict(row)
        payload = data["payload"]
        if isinstance(payload, str):
            payload = json.loads(payload)
        return cls(
            id=str(data["id"]),
            kind=str(data["kind"]),
            payload=dict(payload),
            idempotency_key=str(data["idempotency_key"]),
            state=JobState(str(data["state"])),
            attempts=int(data["attempts"]),
            run_after=str(data["run_after"]),
            last_error=data["last_error"],
            leased_until=data["leased_until"],
            claimed_by=data["claimed_by"],
            created_at=str(data["created_at"]),
            updated_at=str(data["updated_at"]),
        )


def backoff_seconds(attempts: int) -> int:
    delay = int(2 ** max(1, attempts))
    return MAX_BACKOFF_SECONDS if delay > MAX_BACKOFF_SECONDS else delay


def enqueue(
    conn: sqlcipher.Connection,
    *,
    kind: str,
    payload: dict[str, Any],
    idempotency_key: str,
    run_after: datetime | None = None,
    now: datetime | None = None,
) -> Job:
    """Insert a job. Re-submitting the same idempotency key returns the existing row."""
    existing = conn.execute(
        "SELECT * FROM jobs WHERE idempotency_key = ?", (idempotency_key,)
    ).fetchone()
    if existing is not None:
        return Job.from_row(existing)

    moment = now or datetime.now(UTC)
    stamp = iso_now(moment)
    scheduled = iso_now(run_after or moment)
    job_id = new_id()
    conn.execute(
        """
        INSERT INTO jobs (
            id, created_at, updated_at, kind, payload, idempotency_key,
            state, attempts, run_after, last_error, leased_until, claimed_by
        ) VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, NULL, NULL, NULL)
        """,
        (
            job_id,
            stamp,
            stamp,
            kind,
            json.dumps(payload, sort_keys=True),
            idempotency_key,
            JobState.PENDING.value,
            scheduled,
        ),
    )
    conn.commit()
    return get_job(conn, job_id)


def get_job(conn: sqlcipher.Connection, job_id: str) -> Job:
    row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if row is None:
        raise KeyError(job_id)
    return Job.from_row(row)


def recover_expired_leases(conn: sqlcipher.Connection, *, now: datetime | None = None) -> int:
    """Return expired claims to pending so another worker can take them."""
    stamp = iso_now(now)
    cursor = conn.execute(
        """
        UPDATE jobs
        SET state = ?, claimed_by = NULL, leased_until = NULL, updated_at = ?
        WHERE state = ? AND leased_until IS NOT NULL AND leased_until < ?
        """,
        (JobState.PENDING.value, stamp, JobState.CLAIMED.value, stamp),
    )
    conn.commit()
    return int(cursor.rowcount)


def claim(
    conn: sqlcipher.Connection,
    *,
    worker_id: str,
    lease_seconds: int = DEFAULT_LEASE_SECONDS,
    now: datetime | None = None,
    kind: str | None = None,
) -> Job | None:
    """Claim one due job under a lease. Two connections cannot take the same row."""
    recover_expired_leases(conn, now=now)
    moment = now or datetime.now(UTC)
    stamp = iso_now(moment)
    leased_until = iso_now(moment + timedelta(seconds=lease_seconds))
    kind_sql = "AND kind = ?" if kind is not None else ""
    params: list[Any] = [
        JobState.CLAIMED.value,
        worker_id,
        leased_until,
        stamp,
        JobState.PENDING.value,
        stamp,
    ]
    if kind is not None:
        params.append(kind)
    params.append(JobState.PENDING.value)
    cursor = conn.execute(
        f"""
        UPDATE jobs
        SET state = ?, claimed_by = ?, leased_until = ?, updated_at = ?
        WHERE id = (
            SELECT id FROM jobs
            WHERE state = ? AND run_after <= ? {kind_sql}
            ORDER BY run_after, created_at
            LIMIT 1
        )
        AND state = ?
        RETURNING *
        """,
        params,
    )
    row = cursor.fetchone()
    conn.commit()
    if row is None:
        return None
    return Job.from_row(row)


_ACTIVE_LEASE = """
    id = ?
    AND state = ?
    AND claimed_by = ?
    AND leased_until IS NOT NULL
    AND leased_until >= ?
"""


def complete(
    conn: sqlcipher.Connection,
    job_id: str,
    *,
    worker_id: str,
    now: datetime | None = None,
) -> Job:
    """Mark a job completed. No-op/error unless this worker holds a live lease."""
    stamp = iso_now(now)
    cursor = conn.execute(
        f"""
        UPDATE jobs
        SET state = ?, leased_until = NULL, updated_at = ?
        WHERE {_ACTIVE_LEASE}
        """,
        (
            JobState.COMPLETED.value,
            stamp,
            job_id,
            JobState.CLAIMED.value,
            worker_id,
            stamp,
        ),
    )
    conn.commit()
    if cursor.rowcount != 1:
        raise JobLeaseError(
            f"complete did not match an active lease for job {job_id} worker {worker_id}"
        )
    return get_job(conn, job_id)


def fail(
    conn: sqlcipher.Connection,
    job_id: str,
    error: str,
    *,
    worker_id: str,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    now: datetime | None = None,
) -> Job:
    """Fail or re-queue a claimed job. Never mutates a completed/foreign/expired row."""
    stamp = iso_now(now)
    cursor = conn.execute(
        f"""
        UPDATE jobs
        SET
            attempts = attempts + 1,
            last_error = ?,
            claimed_by = NULL,
            leased_until = NULL,
            updated_at = ?,
            state = CASE
                WHEN attempts + 1 >= ? THEN ?
                ELSE ?
            END,
            run_after = CASE
                WHEN attempts + 1 >= ? THEN run_after
                ELSE strftime(
                    '%Y-%m-%dT%H:%M:%SZ',
                    replace(?, 'Z', ''),
                    '+' || MIN(300, (1 << (attempts + 1))) || ' seconds'
                )
            END
        WHERE {_ACTIVE_LEASE}
        """,
        (
            error,
            stamp,
            max_attempts,
            JobState.FAILED.value,
            JobState.PENDING.value,
            max_attempts,
            stamp,
            job_id,
            JobState.CLAIMED.value,
            worker_id,
            stamp,
        ),
    )
    conn.commit()
    if cursor.rowcount != 1:
        raise JobLeaseError(
            f"fail did not match an active lease for job {job_id} worker {worker_id}"
        )
    return get_job(conn, job_id)


def lease_expired(job: Job, *, now: datetime | None = None) -> bool:
    if job.state != JobState.CLAIMED or job.leased_until is None:
        return False
    moment = now or datetime.now(UTC)
    return parse_iso(job.leased_until) < moment.astimezone(UTC)
