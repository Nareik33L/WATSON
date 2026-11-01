"""Entity resolver: exact alias, embedding merge, provisional create."""

from __future__ import annotations

from pathlib import Path

from tests.replay_support import TEST_KEY
from watson.intelligence.resolver import cosine_similarity, normalize_alias, resolve_candidates
from watson.intelligence.retrieve import retrieve_entity_context
from watson.intelligence.runtime.fake import FakeRuntime
from watson.memory.db import connect
from watson.memory.ids import new_id
from watson.memory.migrate import upgrade_head


def _db(tmp_path: Path):
    path = tmp_path / "watson.db"
    upgrade_head(path, TEST_KEY)
    return connect(path, TEST_KEY, load_vec=False)


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


def test_normalize_and_cosine_helpers() -> None:
    assert normalize_alias("  Acme Q4 Proposal ") == "acme q4 proposal"
    assert cosine_similarity([1.0, 0.0], [1.0, 0.0]) == 1.0
    assert cosine_similarity([1.0, 0.0], [0.0, 1.0]) == 0.0
    assert cosine_similarity([], [1.0]) == 0.0


def test_exact_alias_and_canonical_match(tmp_path: Path) -> None:
    conn = _db(tmp_path)
    try:
        entity_id = _seed_entity(
            conn, typ="project", name="Acme Q4 Proposal", alias="the Acme deal"
        )
        runtime = FakeRuntime()
        rows = resolve_candidates(
            conn,
            runtime,
            project_candidates=["ACME Q4 proposal", "the acme deal"],
            people_candidates=[],
        )
        assert len(rows) == 1
        assert rows[0].entity_id == entity_id
        assert rows[0].created is False
        assert conn.execute("SELECT COUNT(*) FROM entities").fetchone()[0] == 1
    finally:
        conn.close()


def test_embedding_merges_alias_and_unknown_is_provisional(tmp_path: Path) -> None:
    conn = _db(tmp_path)
    try:
        entity_id = _seed_entity(conn, typ="project", name="Internal Onboarding Guide")
        runtime = FakeRuntime(
            recordings={
                "embeddings": {
                    "Internal Onboarding Guide": [1.0, 0.0, 0.0, 0.0],
                    "Onboarding Guide": [0.99, 0.01, 0.0, 0.0],
                    "Priya Natarajan": [0.0, 0.0, 1.0, 0.0],
                }
            }
        )
        rows = resolve_candidates(
            conn,
            runtime,
            project_candidates=["Onboarding Guide"],
            people_candidates=["Priya Natarajan"],
            threshold=0.8,
        )
        by_name = {row.canonical_name: row for row in rows}
        merged = by_name["Internal Onboarding Guide"]
        assert merged.entity_id == entity_id
        assert merged.merged_alias is True
        priya = by_name["Priya Natarajan"]
        assert priya.created is True and priya.provisional is True
        aliases = {
            str(row[0]) for row in conn.execute("SELECT alias FROM entity_aliases").fetchall()
        }
        assert "Onboarding Guide" in aliases
        assert conn.execute("SELECT COUNT(*) FROM entities").fetchone()[0] == 2
        assert (
            conn.execute(
                "SELECT provisional FROM entities WHERE canonical_name = ?",
                ("Priya Natarajan",),
            ).fetchone()[0]
            == 1
        )
    finally:
        conn.close()


def test_fts_retrieve_matches_alias_and_name(tmp_path: Path) -> None:
    conn = _db(tmp_path)
    try:
        _seed_entity(conn, typ="project", name="Acme Q4 Proposal", alias="the Acme deal")
        _seed_entity(conn, typ="person", name="Tom Ashworth")
        hits = retrieve_entity_context(conn, "drafting the Acme Q4 proposal for Priya")
        names = {row.name for row in hits}
        assert "Acme Q4 Proposal" in names
        assert retrieve_entity_context(conn, "") == []
    finally:
        conn.close()
