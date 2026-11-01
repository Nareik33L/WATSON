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


def test_exact_match_wins_over_closer_embedding(tmp_path: Path) -> None:
    """Alias/canonical exact match is checked before embedding similarity."""
    conn = _db(tmp_path)
    try:
        exact_id = _seed_entity(conn, typ="project", name="Acme Q4 Proposal", alias="the Acme deal")
        embed_id = _seed_entity(conn, typ="project", name="Other Deal")
        runtime = FakeRuntime(
            recordings={
                "embeddings": {
                    "the Acme deal": [1.0, 0.0, 0.0, 0.0],
                    "Other Deal": [1.0, 0.0, 0.0, 0.0],
                    "Acme Q4 Proposal": [0.0, 0.0, 0.0, 1.0],
                }
            }
        )
        rows = resolve_candidates(
            conn,
            runtime,
            project_candidates=["the Acme deal"],
            people_candidates=[],
            threshold=0.1,
        )
        assert len(rows) == 1
        assert rows[0].entity_id == exact_id
        assert rows[0].entity_id != embed_id
        assert rows[0].created is False
        assert conn.execute("SELECT COUNT(*) FROM entities").fetchone()[0] == 2
    finally:
        conn.close()


def test_embedding_threshold_below_equals_and_tie_order(tmp_path: Path) -> None:
    conn = _db(tmp_path)
    try:
        near_id = _seed_entity(conn, typ="project", name="Near Guide")
        runtime = FakeRuntime(
            recordings={
                "embeddings": {
                    "Near Guide": [1.0, 0.0, 0.0, 0.0],
                    "JustBelow": [0.79, 0.613, 0.0, 0.0],
                    "OnThreshold": [0.8, 0.6, 0.0, 0.0],
                    "JustAbove": [0.81, 0.586, 0.0, 0.0],
                }
            }
        )
        below = resolve_candidates(
            conn,
            runtime,
            project_candidates=["JustBelow"],
            people_candidates=[],
            threshold=0.80,
        )
        assert below[0].created is True and below[0].provisional is True
        assert below[0].entity_id != near_id
        conn.execute("DELETE FROM entity_aliases WHERE entity_id = ?", (below[0].entity_id,))
        conn.execute("DELETE FROM entities WHERE id = ?", (below[0].entity_id,))
        conn.commit()

        at = resolve_candidates(
            conn,
            runtime,
            project_candidates=["OnThreshold"],
            people_candidates=[],
            threshold=0.80,
        )
        assert at[0].entity_id == near_id
        assert at[0].created is False
        assert at[0].merged_alias is True

        above = resolve_candidates(
            conn,
            runtime,
            project_candidates=["JustAbove"],
            people_candidates=[],
            threshold=0.80,
        )
        assert above[0].entity_id == near_id
        assert above[0].created is False

        alpha = _seed_entity(conn, typ="person", name="Alpha")
        _seed_entity(conn, typ="person", name="Zulu")
        tied = FakeRuntime(
            recordings={
                "embeddings": {
                    "Alpha": [1.0, 0.0, 0.0, 0.0],
                    "Zulu": [1.0, 0.0, 0.0, 0.0],
                    "Query": [1.0, 0.0, 0.0, 0.0],
                }
            }
        )
        winner = resolve_candidates(
            conn,
            tied,
            project_candidates=[],
            people_candidates=["Query"],
            threshold=0.5,
        )
        assert winner[0].entity_id == alpha
        assert winner[0].canonical_name == "Alpha"
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
