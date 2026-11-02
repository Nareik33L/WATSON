"""Alembic 0009 up/down on an empty DB and an M3d-populated DB."""

from __future__ import annotations

from pathlib import Path

from alembic import command

from tests.replay_support import TEST_KEY
from watson.memory.db import connect
from watson.memory.ids import new_id
from watson.memory.migrate import alembic_config, upgrade_head
from watson.pipeline.frames import ensure_working_day

TABLE = "achievement_candidate_claims"
HEAD = "0009_m3_achievement_links"


def _tables(conn: object) -> set[str]:
    return {str(r[0]) for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def test_0009_up_down_on_empty_db(tmp_path: Path) -> None:
    path = tmp_path / "empty.db"
    command.upgrade(alembic_config(path, TEST_KEY), HEAD)
    assert upgrade_head(path, TEST_KEY) == HEAD
    conn = connect(path, TEST_KEY, load_vec=False)
    try:
        assert TABLE in _tables(conn)
        cols = {str(r[1]) for r in conn.execute(f"PRAGMA table_info({TABLE})")}
        assert {"candidate_id", "claim_id"} <= cols
    finally:
        conn.close()
    command.downgrade(alembic_config(path, TEST_KEY), "0008_m3d_daily_summaries")
    conn = connect(path, TEST_KEY, load_vec=False)
    try:
        assert TABLE not in _tables(conn)
        assert "daily_summaries" in _tables(conn)
        assert "achievement_candidates" in _tables(conn)
    finally:
        conn.close()


def test_0009_up_down_preserves_m3d_rows(tmp_path: Path) -> None:
    path = tmp_path / "m3d.db"
    command.upgrade(alembic_config(path, TEST_KEY), "0008_m3d_daily_summaries")
    conn = connect(path, TEST_KEY, load_vec=False)
    try:
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
    finally:
        conn.close()
    command.upgrade(alembic_config(path, TEST_KEY), HEAD)
    conn = connect(path, TEST_KEY, load_vec=False)
    try:
        assert (
            conn.execute(
                "SELECT narrative_md FROM daily_summaries WHERE id=?", (summary_id,)
            ).fetchone()[0]
            == "hello"
        )
        assert TABLE in _tables(conn)
    finally:
        conn.close()
    command.downgrade(alembic_config(path, TEST_KEY), "0008_m3d_daily_summaries")
    conn = connect(path, TEST_KEY, load_vec=False)
    try:
        assert TABLE not in _tables(conn)
        assert (
            conn.execute(
                "SELECT narrative_md FROM daily_summaries WHERE id=?", (summary_id,)
            ).fetchone()[0]
            == "hello"
        )
    finally:
        conn.close()
