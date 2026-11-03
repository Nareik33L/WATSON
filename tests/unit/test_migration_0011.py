"""Alembic 0011 up/down: deals table and segments.deal_id."""

from __future__ import annotations

from pathlib import Path

from alembic import command

from tests.replay_support import TEST_KEY
from tests.unit.test_migration_0009 import _seed_pre_0009, _tables
from watson.memory.db import connect
from watson.memory.ids import new_id
from watson.memory.migrate import alembic_config, upgrade_head
from watson.pipeline.frames import ensure_working_day

TABLE = "deals"
HEAD = "0011_m3f_deals"
PREV = "0010_m4b_corrections"


def test_0011_up_down_on_empty_db(tmp_path: Path) -> None:
    path = tmp_path / "empty.db"
    command.upgrade(alembic_config(path, TEST_KEY), HEAD)
    assert upgrade_head(path, TEST_KEY) == HEAD
    conn = connect(path, TEST_KEY, load_vec=False)
    try:
        assert TABLE in _tables(conn)
        deal_cols = {str(r[1]) for r in conn.execute(f"PRAGMA table_info({TABLE})")}
        assert {
            "working_day_id",
            "title",
            "summary",
            "observation_ids",
            "prompt_version",
            "pipeline_version",
        } <= deal_cols
        segment_cols = {str(r[1]) for r in conn.execute("PRAGMA table_info(segments)")}
        assert "deal_id" in segment_cols
    finally:
        conn.close()
    command.downgrade(alembic_config(path, TEST_KEY), PREV)
    conn = connect(path, TEST_KEY, load_vec=False)
    try:
        assert TABLE not in _tables(conn)
        segment_cols = {str(r[1]) for r in conn.execute("PRAGMA table_info(segments)")}
        assert "deal_id" not in segment_cols
        assert "corrections" in _tables(conn)
    finally:
        conn.close()


def test_0011_preserves_corrections_and_summaries(tmp_path: Path) -> None:
    path = tmp_path / "pre0011.db"
    command.upgrade(alembic_config(path, TEST_KEY), PREV)
    conn = connect(path, TEST_KEY, load_vec=False)
    try:
        seeded = _seed_pre_0009(conn)
        day_id = ensure_working_day(conn, "2026-10-31")
        stamp, summary_id = "2026-10-31T18:30:00Z", new_id()
        conn.execute(
            """INSERT INTO daily_summaries (
                id, created_at, updated_at, working_day_id, narrative_md, sections_json,
                claim_ids, prompt_version, pipeline_version
            ) VALUES (?, ?, ?, ?, 'hello', '{"paragraphs":[],"unverified":[]}', '[]',
                      'daily_summary/v1', 1)""",
            (summary_id, stamp, stamp, day_id),
        )
        conn.commit()
        claims = int(conn.execute("SELECT COUNT(*) FROM claims").fetchone()[0])
    finally:
        conn.close()
    command.upgrade(alembic_config(path, TEST_KEY), HEAD)
    conn = connect(path, TEST_KEY, load_vec=False)
    try:
        assert TABLE in _tables(conn)
        assert int(conn.execute("SELECT COUNT(*) FROM claims").fetchone()[0]) == claims
        assert (
            conn.execute(
                "SELECT narrative_md FROM daily_summaries WHERE id=?", (summary_id,)
            ).fetchone()[0]
            == "hello"
        )
        assert int(conn.execute("SELECT COUNT(*) FROM deals").fetchone()[0]) == 0
        assert seeded["claim_ids"]
    finally:
        conn.close()


def test_0011_delete_deal_nulls_segment(tmp_path: Path) -> None:
    path = tmp_path / "fk.db"
    command.upgrade(alembic_config(path, TEST_KEY), HEAD)
    conn = connect(path, TEST_KEY, load_vec=False)
    try:
        day_id = ensure_working_day(conn, "2026-10-31")
        stamp, deal_id, frame_id, segment_id = (
            "2026-10-31T09:00:00Z",
            new_id(),
            new_id(),
            new_id(),
        )
        conn.execute(
            """
            INSERT INTO frames (
                id, created_at, updated_at, working_day_id, captured_at, bundle_id,
                app_name, window_title_hash, window_title, url_domain, phash, state,
                failure_count, deleted_at, deletion_verified
            ) VALUES (?, ?, ?, ?, ?, 'com.lumen.mail', 'Mail', NULL, 'Acme',
                      NULL, 'abcd', 'extracted', 0, NULL, 0)
            """,
            (frame_id, stamp, stamp, day_id, stamp),
        )
        conn.execute(
            """
            INSERT INTO deals (
                id, created_at, updated_at, working_day_id, title, summary,
                observation_ids, prompt_version, pipeline_version
            ) VALUES (?, ?, ?, ?, 'Acme Q4', 'same deal', '[]', 'clustering/v2', 3)
            """,
            (deal_id, stamp, stamp, day_id),
        )
        conn.execute(
            """
            INSERT INTO segments (
                id, created_at, updated_at, working_day_id, bundle_id, window_title_norm,
                url_domain, started_at, ended_at, frame_count, dwell_seconds, line_changes,
                merged_text, state, expires_at, pipeline_version, start_frame_id, deal_id
            ) VALUES (?, ?, ?, ?, 'com.lumen.mail', 'acme', NULL, ?, ?, 1, 60, 1,
                      'pricing', 'closed', NULL, 3, ?, ?)
            """,
            (segment_id, stamp, stamp, day_id, stamp, stamp, frame_id, deal_id),
        )
        conn.commit()
        assert (
            conn.execute("SELECT deal_id FROM segments WHERE id = ?", (segment_id,)).fetchone()[0]
            == deal_id
        )
        conn.execute("DELETE FROM deals WHERE id = ?", (deal_id,))
        conn.commit()
        assert (
            conn.execute("SELECT deal_id FROM segments WHERE id = ?", (segment_id,)).fetchone()[0]
            is None
        )
    finally:
        conn.close()
