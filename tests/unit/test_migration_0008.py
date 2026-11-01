"""Alembic 0008 up/down on an empty DB and an M3c-populated DB."""

from __future__ import annotations

from pathlib import Path

from alembic import command

from tests.replay_support import TEST_KEY
from watson.memory.db import connect
from watson.memory.ids import new_id
from watson.memory.migrate import alembic_config, upgrade_head
from watson.pipeline.frames import ensure_working_day

TABLE = "daily_summaries"


def _tables(conn: object) -> set[str]:
    return {str(r[0]) for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def test_0008_up_down_on_empty_db(tmp_path: Path) -> None:
    path = tmp_path / "empty.db"
    command.upgrade(alembic_config(path, TEST_KEY), "0008_m3d_daily_summaries")
    assert upgrade_head(path, TEST_KEY) == "0008_m3d_daily_summaries"
    conn = connect(path, TEST_KEY, load_vec=False)
    try:
        assert TABLE in _tables(conn)
        cols = {str(r[1]) for r in conn.execute(f"PRAGMA table_info({TABLE})")}
        assert {
            "narrative_md",
            "sections_json",
            "claim_ids",
            "prompt_version",
            "pipeline_version",
        } <= cols
    finally:
        conn.close()
    command.downgrade(alembic_config(path, TEST_KEY), "0007_m3c_consolidation")
    conn = connect(path, TEST_KEY, load_vec=False)
    try:
        assert TABLE not in _tables(conn)
        assert "activities" in _tables(conn)
    finally:
        conn.close()


def test_0008_up_down_preserves_m3c_rows(tmp_path: Path) -> None:
    path = tmp_path / "m3c.db"
    command.upgrade(alembic_config(path, TEST_KEY), "0007_m3c_consolidation")
    conn = connect(path, TEST_KEY, load_vec=False)
    try:
        day_id = ensure_working_day(conn, "2026-10-31")
        stamp, act_id = "2026-10-31T18:30:00Z", new_id()
        conn.execute(
            """INSERT INTO activities (
                id, created_at, updated_at, working_day_id, title, summary,
                observation_ids, prompt_version, pipeline_version
            ) VALUES (?, ?, ?, ?, 'Acme', 'editing', '[]', 'clustering/v1', 1)""",
            (act_id, stamp, stamp, day_id),
        )
        conn.commit()
    finally:
        conn.close()
    command.upgrade(alembic_config(path, TEST_KEY), "0008_m3d_daily_summaries")
    conn = connect(path, TEST_KEY, load_vec=False)
    try:
        assert conn.execute("SELECT title FROM activities WHERE id=?", (act_id,)).fetchone()[0] == (
            "Acme"
        )
        conn.execute(
            """INSERT INTO daily_summaries (
                id, created_at, updated_at, working_day_id, narrative_md, sections_json,
                claim_ids, prompt_version, pipeline_version
            ) VALUES (?, ?, ?, ?, 'hello', '{"paragraphs":[],"unverified":[]}', '[]',
                      'daily_summary/v1', 1)""",
            (new_id(), stamp, stamp, day_id),
        )
        conn.commit()
        assert conn.execute("SELECT COUNT(*) FROM daily_summaries").fetchone()[0] == 1
    finally:
        conn.close()
    command.downgrade(alembic_config(path, TEST_KEY), "0007_m3c_consolidation")
    conn = connect(path, TEST_KEY, load_vec=False)
    try:
        assert TABLE not in _tables(conn)
        assert conn.execute("SELECT title FROM activities WHERE id=?", (act_id,)).fetchone()[0] == (
            "Acme"
        )
    finally:
        conn.close()
