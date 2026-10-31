"""Job queue: enqueue, lease claim, retry/backoff, expired recovery, concurrency."""

from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from watson.memory.db import connect, iso_now
from watson.memory.migrate import upgrade_head
from watson.pipeline.jobs import (
    JobLeaseError,
    JobState,
    backoff_seconds,
    claim,
    complete,
    enqueue,
    fail,
    get_job,
    recover_expired_leases,
)

TEST_KEY = bytes.fromhex("11" * 32)


def _db(tmp_path: Path):
    path = tmp_path / "watson.db"
    upgrade_head(path, TEST_KEY)
    return path, connect(path, TEST_KEY, load_vec=False)


def test_enqueue_claim_complete(tmp_path: Path) -> None:
    _path, conn = _db(tmp_path)
    try:
        job = enqueue(
            conn,
            kind="extract",
            payload={"frame_id": "f1"},
            idempotency_key="extract:f1",
        )
        again = enqueue(
            conn,
            kind="extract",
            payload={"frame_id": "f1"},
            idempotency_key="extract:f1",
        )
        assert again.id == job.id
        taken = claim(conn, worker_id="w1")
        assert taken is not None
        assert taken.id == job.id
        assert taken.state == JobState.CLAIMED
        assert taken.claimed_by == "w1"
        assert claim(conn, worker_id="w2") is None
        done = complete(conn, taken.id, worker_id="w1")
        assert done.state == JobState.COMPLETED
        assert claim(conn, worker_id="w2") is None
    finally:
        conn.close()


def test_fail_retries_with_backoff_then_gives_up(tmp_path: Path) -> None:
    _path, conn = _db(tmp_path)
    try:
        now = datetime(2026, 10, 31, 12, 0, tzinfo=UTC)
        enqueue(
            conn,
            kind="extract",
            payload={},
            idempotency_key="x",
            now=now,
        )
        claimed = claim(conn, worker_id="w1", now=now)
        assert claimed is not None
        retried = fail(conn, claimed.id, "boom", worker_id="w1", max_attempts=3, now=now)
        assert retried.state == JobState.PENDING
        assert retried.attempts == 1
        assert retried.run_after == iso_now(now + timedelta(seconds=backoff_seconds(1)))
        assert claim(conn, worker_id="w1", now=now) is None
        later = now + timedelta(seconds=backoff_seconds(1))
        claimed2 = claim(conn, worker_id="w1", now=later)
        assert claimed2 is not None
        fail(conn, claimed2.id, "boom", worker_id="w1", max_attempts=3, now=later)
        later2 = later + timedelta(seconds=backoff_seconds(2))
        claimed3 = claim(conn, worker_id="w1", now=later2)
        assert claimed3 is not None
        dead = fail(conn, claimed3.id, "boom", worker_id="w1", max_attempts=3, now=later2)
        assert dead.state == JobState.FAILED
        assert dead.attempts == 3
        assert claim(conn, worker_id="w1", now=later2 + timedelta(hours=1)) is None
    finally:
        conn.close()


def test_expired_lease_is_recovered(tmp_path: Path) -> None:
    _path, conn = _db(tmp_path)
    try:
        now = datetime(2026, 10, 31, 12, 0, tzinfo=UTC)
        enqueue(conn, kind="extract", payload={}, idempotency_key="x", now=now)
        claimed = claim(conn, worker_id="w1", lease_seconds=30, now=now)
        assert claimed is not None
        assert claim(conn, worker_id="w2", now=now) is None
        recovered = recover_expired_leases(conn, now=now + timedelta(seconds=31))
        assert recovered == 1
        taken = claim(conn, worker_id="w2", now=now + timedelta(seconds=31))
        assert taken is not None
        assert taken.id == claimed.id
        assert taken.claimed_by == "w2"
    finally:
        conn.close()


def test_two_connections_never_double_claim(tmp_path: Path) -> None:
    path, setup = _db(tmp_path)
    setup.close()
    n = 25
    seed = connect(path, TEST_KEY, load_vec=False)
    try:
        for i in range(n):
            enqueue(seed, kind="extract", payload={"i": i}, idempotency_key=f"k{i}")
    finally:
        seed.close()

    claimed: list[str] = []
    lock = threading.Lock()
    barrier = threading.Barrier(2)
    errors: list[BaseException] = []

    def worker(name: str) -> None:
        conn = connect(path, TEST_KEY, load_vec=False)
        try:
            barrier.wait(timeout=5)
            while True:
                job = claim(conn, worker_id=name)
                if job is None:
                    break
                with lock:
                    claimed.append(job.id)
        except BaseException as exc:  # pragma: no cover - surfaced below
            errors.append(exc)
        finally:
            conn.close()

    threads = [
        threading.Thread(target=worker, args=("a",)),
        threading.Thread(target=worker, args=("b",)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
        assert not thread.is_alive()

    assert errors == []
    assert len(claimed) == n
    assert len(set(claimed)) == n


def test_single_job_two_claimers(tmp_path: Path) -> None:
    path, setup = _db(tmp_path)
    enqueue(setup, kind="extract", payload={}, idempotency_key="only")
    setup.close()

    barrier = threading.Barrier(2)
    results: list[str | None] = [None, None]

    def worker(index: int) -> None:
        conn = connect(path, TEST_KEY, load_vec=False)
        try:
            barrier.wait(timeout=5)
            job = claim(conn, worker_id=f"w{index}")
            results[index] = job.id if job is not None else None
        finally:
            conn.close()

    threads = [
        threading.Thread(target=worker, args=(0,)),
        threading.Thread(target=worker, args=(1,)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
        assert not thread.is_alive()

    winners = [item for item in results if item is not None]
    assert len(winners) == 1


def test_expired_lease_worker_cannot_complete_reclaimed_job(tmp_path: Path) -> None:
    path, setup = _db(tmp_path)
    now = datetime(2026, 10, 31, 12, 0, tzinfo=UTC)
    enqueue(setup, kind="extract", payload={}, idempotency_key="x", now=now)
    setup.close()

    conn_a = connect(path, TEST_KEY, load_vec=False)
    conn_b = connect(path, TEST_KEY, load_vec=False)
    try:
        first = claim(conn_a, worker_id="w1", lease_seconds=30, now=now)
        assert first is not None
        later = now + timedelta(seconds=31)
        recover_expired_leases(conn_b, now=later)
        second = claim(conn_b, worker_id="w2", now=later)
        assert second is not None
        assert second.id == first.id
        assert second.claimed_by == "w2"
        with pytest.raises(JobLeaseError, match="complete"):
            complete(conn_a, first.id, worker_id="w1", now=later)
        still = get_job(conn_b, first.id)
        assert still.state == JobState.CLAIMED
        assert still.claimed_by == "w2"
        done = complete(conn_b, second.id, worker_id="w2", now=later)
        assert done.state == JobState.COMPLETED
    finally:
        conn_a.close()
        conn_b.close()


def test_fail_after_complete_does_not_repend(tmp_path: Path) -> None:
    _path, conn = _db(tmp_path)
    try:
        enqueue(conn, kind="extract", payload={}, idempotency_key="x")
        taken = claim(conn, worker_id="w1")
        assert taken is not None
        complete(conn, taken.id, worker_id="w1")
        with pytest.raises(JobLeaseError, match="fail"):
            fail(conn, taken.id, "late", worker_id="w1")
        assert get_job(conn, taken.id).state == JobState.COMPLETED
    finally:
        conn.close()


def test_complete_unclaimed_job_raises(tmp_path: Path) -> None:
    _path, conn = _db(tmp_path)
    try:
        job = enqueue(conn, kind="extract", payload={}, idempotency_key="x")
        with pytest.raises(JobLeaseError, match="complete"):
            complete(conn, job.id, worker_id="w1")
        assert get_job(conn, job.id).state == JobState.PENDING
    finally:
        conn.close()
