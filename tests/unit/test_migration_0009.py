"""Alembic 0009 up/down on an empty DB and an M3d-populated DB."""

from __future__ import annotations

import re
from pathlib import Path

from alembic import command

from tests.replay_support import TEST_KEY
from watson.memory.db import connect
from watson.memory.ids import new_id
from watson.memory.migrate import alembic_config, upgrade_head
from watson.pipeline.frames import ensure_working_day

TABLE = "achievement_candidate_claims"
HEAD = "0009_m3_achievement_links"
UNIQUE_CLAIM_ID = re.compile(r"UNIQUE\s*\(\s*claim_id\s*\)", re.IGNORECASE)


def _tables(conn: object) -> set[str]:
    return {str(r[0]) for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _count(conn: object, table: str) -> int:
    return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


def _evidence_pairs(conn: object) -> set[tuple[str, str]]:
    return {
        (str(row[0]), str(row[1]))
        for row in conn.execute("SELECT claim_id, snippet FROM claim_evidence")
    }


def _candidate_pairs(conn: object) -> set[tuple[str, str]]:
    return {
        (str(row[0]), str(row[1]))
        for row in conn.execute("SELECT id, claim_id FROM achievement_candidates")
    }


def _claim_ids(conn: object) -> set[str]:
    return {str(row[0]) for row in conn.execute("SELECT id FROM claims")}


def _seed_pre_0009(conn: object) -> dict[str, object]:
    """Claims, evidence, and candidates that 0008 already persisted."""
    day_id = ensure_working_day(conn, "2026-10-31")
    stamp = "2026-10-31T09:00:00Z"
    texts = ["Shipped onboarding", "Shipped onboarding", "Closed pricing"]
    claim_ids: list[str] = []
    candidate_ids: list[str] = []
    evidence: list[tuple[str, str]] = []
    for index, text in enumerate(texts):
        frame_id, segment_id, obs_id, claim_id, candidate_id = (new_id() for _ in range(5))
        started = f"2026-10-31T09:{index:02d}:00Z"
        conn.execute(
            """INSERT INTO frames (
                id, created_at, updated_at, working_day_id, captured_at, bundle_id, app_name,
                window_title_hash, window_title, url_domain, phash, state, failure_count,
                deleted_at, deletion_verified
            ) VALUES (?, ?, ?, ?, ?, 'com.lumen.mail', 'Lumen Mail', NULL, 'Inbox',
                      NULL, 'abcd', 'extracted', 0, NULL, 0)""",
            (frame_id, stamp, stamp, day_id, started),
        )
        conn.execute(
            """INSERT INTO segments (
                id, created_at, updated_at, working_day_id, bundle_id, window_title_norm,
                url_domain, started_at, ended_at, frame_count, dwell_seconds, line_changes,
                merged_text, state, expires_at, pipeline_version, start_frame_id
            ) VALUES (?, ?, ?, ?, 'com.lumen.mail', 'inbox', NULL, ?, ?, 1, 0, 0,
                      'merged', 'closed', NULL, 1, ?)""",
            (segment_id, stamp, stamp, day_id, started, started, frame_id),
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
            ) VALUES (?, ?, ?, ?, ?, 'outcome', ?, 'observed', 0.5, NULL, NULL, 1)""",
            (claim_id, stamp, stamp, obs_id, day_id, text),
        )
        conn.execute(
            """INSERT INTO achievement_candidates (
                id, created_at, updated_at, working_day_id, claim_id, text, pipeline_version
            ) VALUES (?, ?, ?, ?, ?, ?, 1)""",
            (candidate_id, stamp, stamp, day_id, claim_id, text),
        )
        claim_ids.append(claim_id)
        candidate_ids.append(candidate_id)
    per_claim = (4, 3, 3)
    snippet_index = 0
    for claim_id, count in zip(claim_ids, per_claim, strict=True):
        for _ in range(count):
            snippet = f"evidence snippet {snippet_index:02d}"
            conn.execute(
                """INSERT INTO claim_evidence (
                    id, created_at, updated_at, claim_id, segment_id, frame_id,
                    snippet, snippet_kind, line_start, line_end
                ) VALUES (?, ?, ?, ?, NULL, NULL, ?, 'ocr', 1, 1)""",
                (new_id(), stamp, stamp, claim_id, snippet),
            )
            evidence.append((claim_id, snippet))
            snippet_index += 1
    conn.commit()
    return {
        "claim_ids": claim_ids,
        "candidate_ids": candidate_ids,
        "evidence": evidence,
    }


def _assert_pre_0009_preserved(conn: object, seeded: dict[str, object]) -> None:
    claim_ids = list(seeded["claim_ids"])
    candidate_ids = list(seeded["candidate_ids"])
    evidence = list(seeded["evidence"])
    assert _count(conn, "claims") == len(claim_ids)
    assert _count(conn, "claim_evidence") == len(evidence)
    assert _count(conn, "achievement_candidates") == len(candidate_ids)
    assert _claim_ids(conn) == set(claim_ids)
    assert _evidence_pairs(conn) == set(evidence)
    assert _candidate_pairs(conn) == set(zip(candidate_ids, claim_ids, strict=True))
    for claim_id, snippet in evidence:
        row = conn.execute(
            """
            SELECT 1 FROM claims c
            JOIN claim_evidence e ON e.claim_id = c.id
            WHERE c.id = ? AND e.snippet = ?
            """,
            (claim_id, snippet),
        ).fetchone()
        assert row is not None, f"evidence {snippet!r} not reachable from claim {claim_id}"
    for candidate_id, claim_id in zip(candidate_ids, claim_ids, strict=True):
        snippets = {
            str(row[0])
            for row in conn.execute(
                """
                SELECT e.snippet
                FROM achievement_candidates a
                JOIN claims c ON c.id = a.claim_id
                JOIN claim_evidence e ON e.claim_id = c.id
                WHERE a.id = ?
                """,
                (candidate_id,),
            )
        }
        expected = {snip for cid, snip in evidence if cid == claim_id}
        assert snippets == expected
        assert snippets, f"candidate {candidate_id} lost its evidence via claim {claim_id}"


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


def test_0009_preserves_claims_evidence_and_candidates(tmp_path: Path) -> None:
    path = tmp_path / "pre0009.db"
    command.upgrade(alembic_config(path, TEST_KEY), "0008_m3d_daily_summaries")
    conn = connect(path, TEST_KEY, load_vec=False)
    try:
        seeded = _seed_pre_0009(conn)
        _assert_pre_0009_preserved(conn, seeded)
    finally:
        conn.close()
    command.upgrade(alembic_config(path, TEST_KEY), HEAD)
    conn = connect(path, TEST_KEY, load_vec=False)
    try:
        _assert_pre_0009_preserved(conn, seeded)
        assert TABLE in _tables(conn)
        ddl = str(
            conn.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
                (TABLE,),
            ).fetchone()[0]
        )
        assert UNIQUE_CLAIM_ID.search(ddl), ddl
        assert re.search(r"UNIQUE\s*\(\s*candidate_id\s*,\s*claim_id\s*\)", ddl, re.I)
        unique_cols = []
        for index_row in conn.execute(f"PRAGMA index_list({TABLE})"):
            if int(index_row[2]) != 1:
                continue
            cols = [str(info[2]) for info in conn.execute(f"PRAGMA index_info({index_row[1]})")]
            unique_cols.append(cols)
        assert ["claim_id"] in unique_cols, unique_cols
    finally:
        conn.close()
    command.downgrade(alembic_config(path, TEST_KEY), "0008_m3d_daily_summaries")
    conn = connect(path, TEST_KEY, load_vec=False)
    try:
        assert TABLE not in _tables(conn)
        _assert_pre_0009_preserved(conn, seeded)
    finally:
        conn.close()
