"""Alembic 0010 up/down is additive: corrections table only."""

from __future__ import annotations

from pathlib import Path

from alembic import command

from tests.replay_support import TEST_KEY
from tests.unit.test_migration_0009 import (
    _assert_pre_0009_preserved,
    _seed_pre_0009,
    _tables,
)
from watson.memory.db import connect
from watson.memory.ids import new_id
from watson.memory.migrate import alembic_config, upgrade_head
from watson.pipeline.frames import ensure_working_day

TABLE = "corrections"
HEAD = "0010_m4b_corrections"
PREV = "0009_m3_achievement_links"


def test_0010_up_down_on_empty_db(tmp_path: Path) -> None:
    path = tmp_path / "empty.db"
    command.upgrade(alembic_config(path, TEST_KEY), HEAD)
    assert upgrade_head(path, TEST_KEY) == HEAD
    conn = connect(path, TEST_KEY, load_vec=False)
    try:
        assert TABLE in _tables(conn)
        cols = {str(r[1]) for r in conn.execute(f"PRAGMA table_info({TABLE})")}
        assert {
            "target_type",
            "target_id",
            "action",
            "new_text",
            "previous_assertion",
            "working_day_id",
            "pipeline_version",
        } <= cols
    finally:
        conn.close()
    command.downgrade(alembic_config(path, TEST_KEY), PREV)
    conn = connect(path, TEST_KEY, load_vec=False)
    try:
        assert TABLE not in _tables(conn)
        assert "achievement_candidate_claims" in _tables(conn)
        assert "claims" in _tables(conn)
    finally:
        conn.close()


def test_0010_preserves_claims_evidence_and_candidates(tmp_path: Path) -> None:
    path = tmp_path / "pre0010.db"
    command.upgrade(alembic_config(path, TEST_KEY), PREV)
    conn = connect(path, TEST_KEY, load_vec=False)
    try:
        seeded = _seed_pre_0009(conn)
        conn.execute(
            """
            INSERT INTO achievement_candidate_claims (
                id, created_at, updated_at, candidate_id, claim_id
            ) VALUES (?, '2026-10-31T18:30:00Z', '2026-10-31T18:30:00Z', ?, ?)
            """,
            (new_id(), list(seeded["candidate_ids"])[0], list(seeded["claim_ids"])[0]),
        )
        conn.commit()
        _assert_pre_0009_preserved(conn, seeded)
        links = int(conn.execute("SELECT COUNT(*) FROM achievement_candidate_claims").fetchone()[0])
    finally:
        conn.close()
    command.upgrade(alembic_config(path, TEST_KEY), HEAD)
    conn = connect(path, TEST_KEY, load_vec=False)
    try:
        _assert_pre_0009_preserved(conn, seeded)
        assert TABLE in _tables(conn)
        assert (
            int(conn.execute("SELECT COUNT(*) FROM achievement_candidate_claims").fetchone()[0])
            == links
        )
        assert int(conn.execute("SELECT COUNT(*) FROM corrections").fetchone()[0]) == 0
    finally:
        conn.close()
    command.downgrade(alembic_config(path, TEST_KEY), PREV)
    conn = connect(path, TEST_KEY, load_vec=False)
    try:
        assert TABLE not in _tables(conn)
        _assert_pre_0009_preserved(conn, seeded)
        assert "achievement_candidate_claims" in _tables(conn)
        assert (
            int(conn.execute("SELECT COUNT(*) FROM achievement_candidate_claims").fetchone()[0])
            == links
        )
    finally:
        conn.close()


def test_0010_upgrade_head_from_populated_0009(tmp_path: Path) -> None:
    path = tmp_path / "m3.db"
    command.upgrade(alembic_config(path, TEST_KEY), PREV)
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
    assert upgrade_head(path, TEST_KEY) == HEAD
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
