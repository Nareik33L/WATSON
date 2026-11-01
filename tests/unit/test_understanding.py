"""Understanding worker: eligibility, persist, FakeRuntime acme_q4 replay."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from tests.replay_support import DAY_ACME_Q4, TEST_KEY, open_test_db
from watson.config.schema import IntelligenceConfig
from watson.intelligence.runtime import UNDERSTAND_JOB_KIND, FakeRuntime
from watson.intelligence.runtime import http as http_mod
from watson.intelligence.runtime.types import RuntimeUnavailable
from watson.memory.db import connect
from watson.memory.ids import new_id
from watson.memory.migrate import upgrade_head
from watson.pipeline.backlog import drain_backlog
from watson.pipeline.frames import PIPELINE_VERSION
from watson.pipeline.jobs import JobState, claim, enqueue, get_job
from watson.pipeline.replay import replay_fixture
from watson.pipeline.understanding import (
    drain_understand_jobs,
    eligible_segment_ids,
    enqueue_eligible_understand_jobs,
    number_merged_text,
)

ACME_RECORDINGS = Path(__file__).resolve().parents[1] / "fixtures" / "runtime" / "acme_q4.json"


def _counts(conn: object) -> tuple[int, int, int, int, int]:
    return (
        int(conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0]),
        int(conn.execute("SELECT COUNT(*) FROM claims").fetchone()[0]),
        int(conn.execute("SELECT COUNT(*) FROM claim_evidence").fetchone()[0]),
        int(conn.execute("SELECT COUNT(*) FROM entities").fetchone()[0]),
        int(conn.execute("SELECT COUNT(*) FROM entity_aliases").fetchone()[0]),
    )


def _orphans(conn: object) -> list[str]:
    return [
        str(row[0])
        for row in conn.execute(
            """
            SELECT c.id FROM claims c
            LEFT JOIN claim_evidence e ON e.claim_id = c.id
            WHERE e.id IS NULL
            """
        )
    ]


def _seed_entity(conn: object, *, typ: str, name: str, alias: str | None = None) -> str:
    stamp, entity_id = "2026-10-31T09:00:00Z", new_id()
    conn.execute(
        """
        INSERT INTO entities (
            id, created_at, updated_at, type, canonical_name, description,
            first_seen, last_seen, provisional, merged_into_id
        ) VALUES (?, ?, ?, ?, ?, NULL, ?, ?, 0, NULL)
        """,
        (entity_id, stamp, stamp, typ, name, stamp, stamp),
    )
    if alias:
        conn.execute(
            """
            INSERT INTO entity_aliases (
                id, created_at, updated_at, entity_id, alias, source
            ) VALUES (?, ?, ?, ?, ?, 'seed')
            """,
            (new_id(), stamp, stamp, entity_id, alias),
        )
    conn.commit()
    return entity_id


def test_number_merged_text_and_eligibility_helpers() -> None:
    assert number_merged_text("one\n\ntwo", max_chars=32) == "1. one\n2. two"
    assert number_merged_text("", max_chars=8) == "(empty)"


def test_eligibility_closed_or_twenty_minutes(tmp_path: Path) -> None:
    path = tmp_path / "watson.db"
    upgrade_head(path, TEST_KEY)
    conn = connect(path, TEST_KEY, load_vec=False)
    try:
        day_id = new_id()
        stamp = "2026-10-31T09:00:00Z"
        conn.execute(
            """
            INSERT INTO working_days (
                id, created_at, updated_at, date, started_at, ended_at,
                state, consolidation_error, pipeline_version
            ) VALUES (?, ?, ?, '2026-10-31', ?, NULL, 'open', NULL, 1)
            """,
            (day_id, stamp, stamp, stamp),
        )
        specs = (
            ("open-short", "open", 60),
            ("open-long", "open", 20 * 60),
            ("closed-short", "closed", 30),
        )
        ids: dict[str, str] = {}
        for name, state, dwell in specs:
            frame_id, segment_id = new_id(), new_id()
            ids[name] = segment_id
            conn.execute(
                """
                INSERT INTO frames (
                    id, created_at, updated_at, working_day_id, captured_at, bundle_id,
                    app_name, window_title_hash, window_title, url_domain, phash, state,
                    failure_count, deleted_at, deletion_verified
                ) VALUES (?, ?, ?, ?, ?, 'com.lumen.mail', 'Mail', NULL, 'Inbox',
                          NULL, 'abcd', 'extracted', 0, NULL, 0)
                """,
                (frame_id, stamp, stamp, day_id, stamp),
            )
            conn.execute(
                """
                INSERT INTO segments (
                    id, created_at, updated_at, working_day_id, bundle_id, window_title_norm,
                    url_domain, started_at, ended_at, frame_count, dwell_seconds, line_changes,
                    merged_text, state, expires_at, pipeline_version, start_frame_id
                ) VALUES (?, ?, ?, ?, 'com.lumen.mail', ?, NULL, ?, ?, 1, ?, 0,
                          'body', ?, NULL, 1, ?)
                """,
                (segment_id, stamp, stamp, day_id, name, stamp, stamp, dwell, state, frame_id),
            )
        conn.commit()
        eligible = set(eligible_segment_ids(conn))
        assert ids["open-short"] not in eligible
        assert ids["open-long"] in eligible
        assert ids["closed-short"] in eligible
        added = enqueue_eligible_understand_jobs(conn)
        assert added == 2
        assert enqueue_eligible_understand_jobs(conn) == 0
    finally:
        conn.close()


def test_extract_drain_does_not_claim_understand_jobs(tmp_path: Path) -> None:
    path = tmp_path / "watson.db"
    upgrade_head(path, TEST_KEY)
    conn = connect(path, TEST_KEY, load_vec=False)
    try:
        job = enqueue(
            conn,
            kind=UNDERSTAND_JOB_KIND,
            payload={"segment_id": "s1", "pipeline_version": 1},
            idempotency_key="understand:s1:1",
        )
        assert drain_backlog(conn, limit=4, fixture_dir=tmp_path) == 0
        assert get_job(conn, job.id).state is JobState.PENDING
        taken = claim(conn, worker_id="w", kind=UNDERSTAND_JOB_KIND)
        assert taken is not None and taken.id == job.id
    finally:
        conn.close()


def test_missing_runtime_leaves_understand_jobs_queued(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "watson.db"
    upgrade_head(path, TEST_KEY)
    conn = connect(path, TEST_KEY, load_vec=False)
    try:
        enqueue(
            conn,
            kind=UNDERSTAND_JOB_KIND,
            payload={"segment_id": "s1", "pipeline_version": 1},
            idempotency_key="understand:s1:1",
        )

        def boom(*_a: object, **_k: object) -> dict[str, Any]:
            raise RuntimeUnavailable("Ollama is not reachable at http://127.0.0.1:1")

        monkeypatch.setattr(http_mod, "request_json", boom)
        completed = drain_understand_jobs(
            conn, config=IntelligenceConfig(runtime="ollama", ollama_host="http://127.0.0.1:1")
        )
        assert completed == 0
        row = conn.execute("SELECT state, last_error, attempts FROM jobs").fetchone()
        assert row["state"] == JobState.PENDING.value
        assert row["attempts"] == 0
        assert "Ollama is not reachable" in str(row["last_error"])
        assert conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0] == 0
    finally:
        conn.close()


def test_acme_q4_fake_runtime_replay(data_dir: Path, key_provider: object, tmp_path: Path) -> None:
    conn = open_test_db(data_dir)
    try:
        result = replay_fixture(DAY_ACME_Q4, conn, instant=True, spool_dir=data_dir / "spool-acme")
        assert result.segments >= 1
        _seed_entity(conn, typ="project", name="Acme Q4 Proposal", alias="the Acme deal")
        _seed_entity(conn, typ="project", name="Internal Onboarding Guide")
        _seed_entity(conn, typ="person", name="Tom Ashworth")
        runtime = FakeRuntime(recordings=ACME_RECORDINGS)
        first = drain_understand_jobs(conn, runtime=runtime, embedding=runtime)
        assert first >= 1
        assert _orphans(conn) == []
        obs, claims, evidence, entities, aliases = _counts(conn)
        assert obs == first
        assert evidence >= claims
        assert claims == int(
            conn.execute("SELECT COUNT(DISTINCT claim_id) FROM claim_evidence").fetchone()[0]
        )
        versions = {
            (str(row[0]), int(row[1]))
            for row in conn.execute(
                "SELECT DISTINCT prompt_version, pipeline_version FROM observations"
            )
        }
        assert versions == {("understanding/v1", PIPELINE_VERSION)}

        acme = conn.execute(
            "SELECT id FROM entities WHERE canonical_name = ?", ("Acme Q4 Proposal",)
        ).fetchone()
        assert acme is not None
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM entities WHERE canonical_name = ?",
                ("Acme Q4 Proposal",),
            ).fetchone()[0]
            == 1
        )
        onboarding_aliases = {
            str(row[0])
            for row in conn.execute(
                """
                SELECT a.alias FROM entity_aliases a
                JOIN entities e ON e.id = a.entity_id
                WHERE e.canonical_name = ?
                """,
                ("Internal Onboarding Guide",),
            )
        }
        assert "Onboarding Guide" in onboarding_aliases
        priya = conn.execute(
            """
            SELECT provisional FROM entities
            WHERE type = 'person' AND canonical_name = ?
            """,
            ("Priya Natarajan",),
        ).fetchone()
        assert priya is not None and int(priya[0]) == 1

        second = drain_understand_jobs(conn, runtime=runtime, embedding=runtime)
        assert second == 0
        assert _counts(conn) == (obs, claims, evidence, entities, aliases)
        assert _orphans(conn) == []
        pending = conn.execute(
            "SELECT COUNT(*) FROM jobs WHERE kind = ? AND state = ?",
            (UNDERSTAND_JOB_KIND, JobState.PENDING.value),
        ).fetchone()[0]
        assert pending == 0
    finally:
        conn.close()
