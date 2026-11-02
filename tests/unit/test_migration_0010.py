"""Alembic 0010 up/down is additive: corrections table only."""

from __future__ import annotations

from pathlib import Path

import pytest
from alembic import command

from tests.replay_support import TEST_KEY
from tests.unit.test_migration_0009 import (
    _assert_pre_0009_preserved,
    _seed_pre_0009,
    _tables,
)
from watson.memory.corrections import rename_entity
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


def _pragma_info(conn: object, table: str) -> list[tuple[object, ...]]:
    return [tuple(row) for row in conn.execute(f"PRAGMA table_info({table})")]


def _table_sql(conn: object, table: str) -> str:
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
    ).fetchone()
    assert row is not None
    return str(row[0])


def _alias_rows(conn: object) -> list[tuple[str, str, str]]:
    return [
        (str(row[0]), str(row[1]), str(row[2]))
        for row in conn.execute(
            "SELECT entity_id, alias, source FROM entity_aliases ORDER BY entity_id, alias"
        )
    ]


def _assertion_rows(conn: object) -> list[tuple[str, str]]:
    return [
        (str(row[0]), str(row[1]))
        for row in conn.execute("SELECT id, assertion FROM claims ORDER BY id")
    ]


def _insert_entity_with_aliases(conn: object) -> str:
    stamp, entity_id = "2026-10-31T09:00:00Z", new_id()
    conn.execute(
        """
        INSERT INTO entities (
            id, created_at, updated_at, type, canonical_name, description,
            first_seen, last_seen, provisional, merged_into_id
        ) VALUES (?, ?, ?, 'project', 'Acme Q4 Proposal', NULL, ?, ?, 0, NULL)
        """,
        (entity_id, stamp, stamp, stamp, stamp),
    )
    conn.execute(
        """
        INSERT INTO entity_aliases (
            id, created_at, updated_at, entity_id, alias, source
        ) VALUES (?, ?, ?, ?, 'Acme', 'model')
        """,
        (new_id(), stamp, stamp, entity_id),
    )
    conn.execute(
        """
        INSERT INTO entity_aliases (
            id, created_at, updated_at, entity_id, alias, source
        ) VALUES (?, ?, ?, ?, 'The Deal', 'user')
        """,
        (new_id(), stamp, stamp, entity_id),
    )
    conn.commit()
    return entity_id


def test_0010_leaves_aliases_and_assertions_untouched(tmp_path: Path) -> None:
    path = tmp_path / "pre0010-aliases.db"
    command.upgrade(alembic_config(path, TEST_KEY), PREV)
    conn = connect(path, TEST_KEY, load_vec=False)
    try:
        seeded = _seed_pre_0009(conn)
        claim_ids = list(seeded["claim_ids"])
        conn.execute("UPDATE claims SET assertion = 'inferred' WHERE id = ?", (claim_ids[1],))
        conn.execute("UPDATE claims SET assertion = 'unconfirmed' WHERE id = ?", (claim_ids[2],))
        _insert_entity_with_aliases(conn)
        aliases = _alias_rows(conn)
        assertions = _assertion_rows(conn)
        alias_cols = _pragma_info(conn, "entity_aliases")
        claim_cols = _pragma_info(conn, "claims")
        alias_sql = _table_sql(conn, "entity_aliases")
        claim_sql = _table_sql(conn, "claims")
        assert ("inferred" in {row[1] for row in assertions}) and (
            "unconfirmed" in {row[1] for row in assertions}
        )
        assert any(str(col[1]) == "assertion" for col in claim_cols)
    finally:
        conn.close()
    command.upgrade(alembic_config(path, TEST_KEY), HEAD)
    conn = connect(path, TEST_KEY, load_vec=False)
    try:
        assert TABLE in _tables(conn)
        assert _alias_rows(conn) == aliases
        assert _assertion_rows(conn) == assertions
        assert _pragma_info(conn, "entity_aliases") == alias_cols
        assert _pragma_info(conn, "claims") == claim_cols
        assert _table_sql(conn, "entity_aliases") == alias_sql
        assert _table_sql(conn, "claims") == claim_sql
        assert int(conn.execute("SELECT COUNT(*) FROM corrections").fetchone()[0]) == 0
    finally:
        conn.close()


def test_0010_downgrade_refuses_when_corrections_exist(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("WATSON_ALLOW_DESTRUCTIVE_DOWNGRADE", raising=False)
    path = tmp_path / "with-corrections.db"
    command.upgrade(alembic_config(path, TEST_KEY), HEAD)
    conn = connect(path, TEST_KEY, load_vec=False)
    try:
        entity_id = _insert_entity_with_aliases(conn)
        rename_entity(conn, entity_id, "Zebra Deal")
        aliases = _alias_rows(conn)
        canonical = str(
            conn.execute(
                "SELECT canonical_name FROM entities WHERE id = ?", (entity_id,)
            ).fetchone()[0]
        )
        corrections = int(conn.execute("SELECT COUNT(*) FROM corrections").fetchone()[0])
        assert corrections == 1
        assert canonical == "Zebra Deal"
    finally:
        conn.close()
    with pytest.raises(RuntimeError, match="Refusing to downgrade migration 0010"):
        command.downgrade(alembic_config(path, TEST_KEY), PREV)
    conn = connect(path, TEST_KEY, load_vec=False)
    try:
        assert TABLE in _tables(conn)
        assert int(conn.execute("SELECT COUNT(*) FROM corrections").fetchone()[0]) == corrections
        assert _alias_rows(conn) == aliases
        assert (
            str(
                conn.execute(
                    "SELECT canonical_name FROM entities WHERE id = ?", (entity_id,)
                ).fetchone()[0]
            )
            == "Zebra Deal"
        )
    finally:
        conn.close()


def test_0010_empty_downgrade_preserves_user_aliases(tmp_path: Path) -> None:
    path = tmp_path / "empty-corrections.db"
    command.upgrade(alembic_config(path, TEST_KEY), PREV)
    conn = connect(path, TEST_KEY, load_vec=False)
    try:
        _insert_entity_with_aliases(conn)
        aliases = _alias_rows(conn)
        alias_cols = _pragma_info(conn, "entity_aliases")
    finally:
        conn.close()
    command.upgrade(alembic_config(path, TEST_KEY), HEAD)
    command.downgrade(alembic_config(path, TEST_KEY), PREV)
    conn = connect(path, TEST_KEY, load_vec=False)
    try:
        assert TABLE not in _tables(conn)
        assert _alias_rows(conn) == aliases
        assert _pragma_info(conn, "entity_aliases") == alias_cols
        assert "The Deal" in {row[1] for row in aliases}
    finally:
        conn.close()


def test_0010_destructive_downgrade_override_drops_corrections(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("WATSON_ALLOW_DESTRUCTIVE_DOWNGRADE", "1")
    path = tmp_path / "override.db"
    command.upgrade(alembic_config(path, TEST_KEY), HEAD)
    conn = connect(path, TEST_KEY, load_vec=False)
    try:
        entity_id = _insert_entity_with_aliases(conn)
        rename_entity(conn, entity_id, "Zebra Deal")
        aliases = _alias_rows(conn)
        assert int(conn.execute("SELECT COUNT(*) FROM corrections").fetchone()[0]) == 1
    finally:
        conn.close()
    command.downgrade(alembic_config(path, TEST_KEY), PREV)
    conn = connect(path, TEST_KEY, load_vec=False)
    try:
        assert TABLE not in _tables(conn)
        assert _alias_rows(conn) == aliases
        assert (
            str(
                conn.execute(
                    "SELECT canonical_name FROM entities WHERE id = ?", (entity_id,)
                ).fetchone()[0]
            )
            == "Zebra Deal"
        )
    finally:
        conn.close()
