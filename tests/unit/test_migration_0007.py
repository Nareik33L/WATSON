"""Alembic 0007 up/down on an empty DB and an M3a-populated DB."""

from __future__ import annotations

from pathlib import Path

from alembic import command

from tests.replay_support import TEST_KEY
from watson.memory.db import connect
from watson.memory.ids import new_id
from watson.memory.migrate import alembic_config, upgrade_head
from watson.pipeline.frames import ensure_working_day

M3C = {
    "activities",
    "decisions",
    "commitments",
    "achievement_candidates",
    "entity_history",
}


def _tables(conn: object) -> set[str]:
    return {str(r[0]) for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _columns(conn: object, table: str) -> set[str]:
    return {str(r[1]) for r in conn.execute(f"PRAGMA table_info({table})")}


def _insert_m3a(conn: object) -> tuple[str, str, str, str]:
    day_id = ensure_working_day(conn, "2026-10-31")
    stamp, frame_id, segment_id = "2026-10-31T09:00:00Z", new_id(), new_id()
    obs_id, claim_id, entity_id = new_id(), new_id(), new_id()
    conn.execute(
        """INSERT INTO frames (
            id, created_at, updated_at, working_day_id, captured_at, bundle_id, app_name,
            window_title_hash, window_title, url_domain, phash, state, failure_count,
            deleted_at, deletion_verified
        ) VALUES (?, ?, ?, ?, ?, 'com.lumen.mail', 'Lumen Mail', NULL, 'Inbox',
                  NULL, 'abcd', 'extracted', 0, NULL, 0)""",
        (frame_id, stamp, stamp, day_id, stamp),
    )
    conn.execute(
        """INSERT INTO segments (
            id, created_at, updated_at, working_day_id, bundle_id, window_title_norm,
            url_domain, started_at, ended_at, frame_count, dwell_seconds, line_changes,
            merged_text, state, expires_at, pipeline_version, start_frame_id
        ) VALUES (?, ?, ?, ?, 'com.lumen.mail', 'inbox', NULL, ?, ?, 1, 0, 0,
                  'merged', 'closed', NULL, 1, ?)""",
        (segment_id, stamp, stamp, day_id, stamp, stamp, frame_id),
    )
    conn.execute(
        """INSERT INTO observations (
            id, created_at, updated_at, segment_id, activity_type, description,
            confidence, model_id, prompt_version, pipeline_version, raw_json
        ) VALUES (?, ?, ?, ?, 'knowledge_work', 'desc', 0.5, 'fake',
                  'understanding/v1', 1, '{}')""",
        (obs_id, stamp, stamp, segment_id),
    )
    conn.execute(
        """INSERT INTO claims (
            id, created_at, updated_at, observation_id, working_day_id, kind, text,
            assertion, confidence, status, due_hint, pipeline_version
        ) VALUES (?, ?, ?, ?, ?, 'outcome', 'shipped', 'observed', 0.5, NULL, NULL, 1)""",
        (claim_id, stamp, stamp, obs_id, day_id),
    )
    conn.execute(
        """INSERT INTO entities (
            id, created_at, updated_at, type, canonical_name, description,
            first_seen, last_seen, provisional, merged_into_id
        ) VALUES (?, ?, ?, 'project', 'Acme Q4 Proposal', NULL, ?, ?, 0, NULL)""",
        (entity_id, stamp, stamp, stamp, stamp),
    )
    conn.commit()
    return day_id, claim_id, entity_id, obs_id


def test_0007_up_down_on_empty_db(tmp_path: Path) -> None:
    path = tmp_path / "empty.db"
    command.upgrade(alembic_config(path, TEST_KEY), "0007_m3c_consolidation")
    assert upgrade_head(path, TEST_KEY) == "0007_m3c_consolidation"
    conn = connect(path, TEST_KEY, load_vec=False)
    try:
        assert M3C <= _tables(conn)
        cols = _columns(conn, "working_days")
        assert "consolidated_at" in cols
        assert "consolidation_gap" in cols
        assert "consolidation_error" in cols
    finally:
        conn.close()
    command.downgrade(alembic_config(path, TEST_KEY), "0006_m3a_understanding")
    conn = connect(path, TEST_KEY, load_vec=False)
    try:
        names = _tables(conn)
        assert not M3C & names
        cols = _columns(conn, "working_days")
        assert "consolidated_at" not in cols
        assert "consolidation_gap" not in cols
        assert {"frames", "observations", "claims"} <= names
    finally:
        conn.close()


def test_0007_up_down_preserves_m3a_rows(tmp_path: Path) -> None:
    path = tmp_path / "m3a.db"
    command.upgrade(alembic_config(path, TEST_KEY), "0006_m3a_understanding")
    conn = connect(path, TEST_KEY, load_vec=False)
    try:
        day_id, claim_id, entity_id, obs_id = _insert_m3a(conn)
    finally:
        conn.close()
    command.upgrade(alembic_config(path, TEST_KEY), "0007_m3c_consolidation")
    conn = connect(path, TEST_KEY, load_vec=False)
    try:
        stamp, act_id = "2026-10-31T18:30:00Z", new_id()
        conn.execute(
            """INSERT INTO activities (
                id, created_at, updated_at, working_day_id, title, summary,
                observation_ids, prompt_version, pipeline_version
            ) VALUES (?, ?, ?, ?, 'Acme', 'editing', ?, 'clustering/v1', 1)""",
            (act_id, stamp, stamp, day_id, f'["{obs_id}"]'),
        )
        conn.execute(
            """INSERT INTO achievement_candidates (
                id, created_at, updated_at, working_day_id, claim_id, text, pipeline_version
            ) VALUES (?, ?, ?, ?, ?, 'shipped', 1)""",
            (new_id(), stamp, stamp, day_id, claim_id),
        )
        conn.execute(
            """INSERT INTO entity_history (
                id, created_at, updated_at, entity_id, working_day_id, delta_text,
                claim_ids, prompt_version, pipeline_version
            ) VALUES (?, ?, ?, ?, ?, 'drafted', ?, 'day_extract/v1', 1)""",
            (new_id(), stamp, stamp, entity_id, day_id, f'["{claim_id}"]'),
        )
        conn.commit()
        assert conn.execute("SELECT title FROM activities").fetchone()[0] == "Acme"
        assert conn.execute("SELECT text FROM claims WHERE id=?", (claim_id,)).fetchone()[0] == (
            "shipped"
        )
    finally:
        conn.close()
    command.downgrade(alembic_config(path, TEST_KEY), "0006_m3a_understanding")
    conn = connect(path, TEST_KEY, load_vec=False)
    try:
        assert not M3C & _tables(conn)
        assert conn.execute("SELECT id FROM claims WHERE id=?", (claim_id,)).fetchone()
        assert conn.execute("SELECT id FROM entities WHERE id=?", (entity_id,)).fetchone()
        assert conn.execute("SELECT id FROM observations WHERE id=?", (obs_id,)).fetchone()
    finally:
        conn.close()
