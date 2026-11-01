"""Alembic 0006 up/down on an empty DB and an M2-populated DB."""

from __future__ import annotations

from pathlib import Path

from alembic import command

from tests.replay_support import TEST_KEY
from watson.memory.db import connect
from watson.memory.ids import new_id
from watson.memory.migrate import alembic_config, upgrade_head
from watson.pipeline.frames import ensure_working_day

M3 = {"observations", "claims", "claim_evidence", "entities", "entity_aliases"}


def _tables(conn: object) -> set[str]:
    return {str(r[0]) for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _insert_m2(conn: object) -> tuple[str, str, str]:
    day_id = ensure_working_day(conn, "2026-10-31")
    stamp, frame_id, segment_id = "2026-10-31T09:00:00Z", new_id(), new_id()
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
        """INSERT INTO frame_text (
            id, created_at, updated_at, frame_id, text_redacted, ocr_confidence, expires_at, source
        ) VALUES (?, ?, ?, ?, 'm2 body', 1.0, NULL, 'fixture_sidecar')""",
        (new_id(), stamp, stamp, frame_id),
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
    conn.commit()
    return day_id, frame_id, segment_id


def test_0006_up_down_on_empty_db(tmp_path: Path) -> None:
    path = tmp_path / "empty.db"
    command.upgrade(alembic_config(path, TEST_KEY), "0006_m3a_understanding")
    assert upgrade_head(path, TEST_KEY) == "0006_m3a_understanding"
    conn = connect(path, TEST_KEY, load_vec=False)
    try:
        assert M3 <= _tables(conn)
    finally:
        conn.close()
    command.downgrade(alembic_config(path, TEST_KEY), "0005_m2c_on_delete")
    conn = connect(path, TEST_KEY, load_vec=False)
    try:
        names = _tables(conn)
        assert not M3 & names
        assert {"frames", "segments", "jobs"} <= names
    finally:
        conn.close()


def test_0006_up_down_preserves_m2_rows(tmp_path: Path) -> None:
    path = tmp_path / "m2.db"
    command.upgrade(alembic_config(path, TEST_KEY), "0005_m2c_on_delete")
    conn = connect(path, TEST_KEY, load_vec=False)
    try:
        day_id, frame_id, segment_id = _insert_m2(conn)
    finally:
        conn.close()
    command.upgrade(alembic_config(path, TEST_KEY), "0006_m3a_understanding")
    conn = connect(path, TEST_KEY, load_vec=False)
    try:
        stamp, obs_id, claim_id, entity_id = "2026-10-31T10:00:00Z", new_id(), new_id(), new_id()
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
            ) VALUES (?, ?, ?, ?, ?, 'activity', 'editing', 'observed', 0.5, NULL, NULL, 1)""",
            (claim_id, stamp, stamp, obs_id, day_id),
        )
        conn.execute(
            """INSERT INTO claim_evidence (
                id, created_at, updated_at, claim_id, segment_id, frame_id,
                snippet, snippet_kind, line_start, line_end
            ) VALUES (?, ?, ?, ?, ?, ?, 'merged', 'ocr', 1, 1)""",
            (new_id(), stamp, stamp, claim_id, segment_id, frame_id),
        )
        conn.execute(
            """INSERT INTO entities (
                id, created_at, updated_at, type, canonical_name, description,
                first_seen, last_seen, provisional, merged_into_id
            ) VALUES (?, ?, ?, 'project', 'Acme Q4 Proposal', NULL, ?, ?, 1, NULL)""",
            (entity_id, stamp, stamp, stamp, stamp),
        )
        conn.execute(
            """INSERT INTO entity_aliases (
                id, created_at, updated_at, entity_id, alias, source
            ) VALUES (?, ?, ?, ?, 'the Acme deal', 'model')""",
            (new_id(), stamp, stamp, entity_id),
        )
        conn.commit()
        assert conn.execute("SELECT provisional FROM entities").fetchone()[0] == 1
        assert conn.execute("SELECT prompt_version FROM observations").fetchone()[0] == (
            "understanding/v1"
        )
    finally:
        conn.close()
    command.downgrade(alembic_config(path, TEST_KEY), "0005_m2c_on_delete")
    conn = connect(path, TEST_KEY, load_vec=False)
    try:
        assert not M3 & _tables(conn)
        assert conn.execute("SELECT id FROM frames WHERE id=?", (frame_id,)).fetchone()
        assert conn.execute("SELECT id FROM segments WHERE id=?", (segment_id,)).fetchone()
        text = conn.execute(
            "SELECT text_redacted FROM frame_text WHERE frame_id=?", (frame_id,)
        ).fetchone()
        assert text is not None and text[0] == "m2 body"
    finally:
        conn.close()
