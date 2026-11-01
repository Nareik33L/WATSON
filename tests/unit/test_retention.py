"""Tier-2 retention sweep: age window, idempotency, and never-outside-tier."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from alembic import command

from tests.replay_support import TEST_KEY, open_test_db, write_mini_fixture
from watson.memory.db import connect, iso_now, parse_iso
from watson.memory.ids import new_id
from watson.memory.migrate import alembic_config, upgrade_head
from watson.pipeline.frames import ensure_working_day
from watson.pipeline.replay import replay_fixture
from watson.privacy.audit import KIND_TIER2_PURGED
from watson.privacy.retention import sweep_tier2


def _stamp(moment: datetime) -> str:
    return iso_now(moment)


def _insert_frame(
    conn: object,
    *,
    day_id: str,
    captured_at: str,
    expires_at: str | None,
    text: str = "tier-two secret line",
    state: str = "extracted",
) -> str:
    stamp = captured_at
    frame_id = new_id()
    conn.execute(
        """
        INSERT INTO frames (
            id, created_at, updated_at, working_day_id, captured_at,
            bundle_id, app_name, window_title_hash, window_title,
            url_domain, phash, state, failure_count, deleted_at, deletion_verified
        ) VALUES (?, ?, ?, ?, ?, 'com.lumen.mail', 'Lumen Mail', NULL, 'Inbox',
                  NULL, 'abcd', ?, 0, NULL, 0)
        """,
        (frame_id, stamp, stamp, day_id, captured_at, state),
    )
    conn.execute(
        """
        INSERT INTO frame_text (
            id, created_at, updated_at, frame_id, text_redacted,
            ocr_confidence, expires_at, source
        ) VALUES (?, ?, ?, ?, ?, 1.0, ?, 'fixture_sidecar')
        """,
        (new_id(), stamp, stamp, frame_id, text, expires_at),
    )
    conn.commit()
    return frame_id


def _insert_segment(
    conn: object,
    *,
    day_id: str,
    frame_id: str,
    started_at: str,
    expires_at: str | None,
    merged: str = "merged tier-two body",
) -> str:
    stamp = started_at
    segment_id = new_id()
    conn.execute(
        """
        INSERT INTO segments (
            id, created_at, updated_at, working_day_id, bundle_id, window_title_norm,
            url_domain, started_at, ended_at, frame_count, dwell_seconds, line_changes,
            merged_text, state, expires_at, pipeline_version, start_frame_id
        ) VALUES (?, ?, ?, ?, 'com.lumen.mail', 'inbox', NULL, ?, ?, 1, 0, 0,
                  ?, 'open', ?, 1, ?)
        """,
        (
            segment_id,
            stamp,
            stamp,
            day_id,
            started_at,
            started_at,
            merged,
            expires_at,
            frame_id,
        ),
    )
    conn.execute(
        """
        INSERT INTO segment_frames (
            id, created_at, updated_at, segment_id, frame_id, captured_at
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (new_id(), stamp, stamp, segment_id, frame_id, started_at),
    )
    conn.commit()
    return segment_id


@pytest.mark.parametrize("grace_days", [7, 3])
def test_expires_at_is_captured_at_plus_tier2_grace_days(
    data_dir: Path, key_provider: object, tmp_path: Path, grace_days: int
) -> None:
    (data_dir / "config.toml").write_text(
        f"""
data_dir = "{data_dir}"

[retention]
tier2_grace_days = {grace_days}
""",
        encoding="utf-8",
    )
    fixture = write_mini_fixture(tmp_path, frames=2)
    conn = open_test_db(data_dir)
    try:
        replay_fixture(fixture, conn, instant=True, spool_dir=data_dir / "spool")
        text_rows = conn.execute(
            """
            SELECT f.captured_at, t.expires_at
            FROM frames f
            JOIN frame_text t ON t.frame_id = f.id
            """
        ).fetchall()
        assert text_rows
        for captured_at, expires_at in text_rows:
            assert captured_at is not None
            assert expires_at is not None
            assert parse_iso(str(expires_at)) - parse_iso(str(captured_at)) == timedelta(
                days=grace_days
            )

        segment_rows = conn.execute(
            """
            SELECT f.captured_at, s.expires_at
            FROM segments s
            JOIN frames f ON f.id = s.start_frame_id
            """
        ).fetchall()
        assert segment_rows
        for captured_at, expires_at in segment_rows:
            assert captured_at is not None
            assert expires_at is not None
            assert parse_iso(str(expires_at)) - parse_iso(str(captured_at)) == timedelta(
                days=grace_days
            )
    finally:
        conn.close()


def test_retention_just_inside_and_just_outside_window(
    data_dir: Path, key_provider: object
) -> None:
    now = datetime(2026, 11, 7, 9, 0, tzinfo=UTC)
    inside = _stamp(now + timedelta(seconds=1))
    outside = _stamp(now)
    conn = open_test_db(data_dir)
    try:
        day_id = ensure_working_day(conn, "2026-10-31")
        keep_id = _insert_frame(conn, day_id=day_id, captured_at=inside, expires_at=inside)
        drop_id = _insert_frame(conn, day_id=day_id, captured_at=outside, expires_at=outside)
        keep_seg = _insert_segment(
            conn, day_id=day_id, frame_id=keep_id, started_at=inside, expires_at=inside
        )
        drop_seg = _insert_segment(
            conn, day_id=day_id, frame_id=drop_id, started_at=outside, expires_at=outside
        )
        jobs_before = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
        frames_before = conn.execute("SELECT COUNT(*) FROM frames").fetchone()[0]

        first = sweep_tier2(conn, now=now)
        assert first.texts == 1
        assert first.segments == 1
        assert first.visuals == 0

        remaining = {
            str(row[0]) for row in conn.execute("SELECT frame_id FROM frame_text").fetchall()
        }
        assert keep_id in remaining
        assert drop_id not in remaining
        keep_merged = conn.execute(
            "SELECT merged_text FROM segments WHERE id = ?", (keep_seg,)
        ).fetchone()[0]
        drop_merged = conn.execute(
            "SELECT merged_text FROM segments WHERE id = ?", (drop_seg,)
        ).fetchone()[0]
        assert keep_merged == "merged tier-two body"
        assert drop_merged == ""
        assert conn.execute("SELECT COUNT(*) FROM frames").fetchone()[0] == frames_before
        assert conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == jobs_before
        assert conn.execute("SELECT COUNT(*) FROM segments").fetchone()[0] == 2
        assert conn.execute("SELECT COUNT(*) FROM segment_frames").fetchone()[0] == 2

        second = sweep_tier2(conn, now=now)
        assert second.texts == 0
        assert second.segments == 0
        events = conn.execute(
            "SELECT COUNT(*) FROM audit_events WHERE kind = ?", (KIND_TIER2_PURGED,)
        ).fetchone()[0]
        assert events == 1
        detail = conn.execute(
            "SELECT detail_json FROM audit_events WHERE kind = ?", (KIND_TIER2_PURGED,)
        ).fetchone()[0]
        assert "merged_text" not in str(detail)
        assert "text_redacted" not in str(detail)
        assert '"texts": 1' in str(detail)
    finally:
        conn.close()


def test_sweep_never_deletes_outside_tier(
    data_dir: Path, key_provider: object, tmp_path: Path
) -> None:
    fixture = write_mini_fixture(tmp_path, frames=2)
    conn = open_test_db(data_dir)
    try:
        replay_fixture(fixture, conn, instant=True, spool_dir=data_dir / "spool")
        frames = conn.execute("SELECT COUNT(*) FROM frames").fetchone()[0]
        jobs = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
        segs = conn.execute("SELECT COUNT(*) FROM segments").fetchone()[0]
        membership = conn.execute("SELECT COUNT(*) FROM segment_frames").fetchone()[0]
        days = conn.execute("SELECT COUNT(*) FROM working_days").fetchone()[0]
        settings = conn.execute("SELECT COUNT(*) FROM settings").fetchone()[0]
        audit = conn.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0]
        titles = [row[0] for row in conn.execute("SELECT window_title FROM frames").fetchall()]
        assert any(title == "Inbox" for title in titles)

        # Far enough in the future that every fixture expires_at is due.
        result = sweep_tier2(conn, now=datetime(2026, 11, 30, 9, 0, tzinfo=UTC))
        assert result.texts == frames
        assert result.segments == segs
        assert conn.execute("SELECT COUNT(*) FROM frames").fetchone()[0] == frames
        assert conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == jobs
        assert conn.execute("SELECT COUNT(*) FROM segments").fetchone()[0] == segs
        assert conn.execute("SELECT COUNT(*) FROM segment_frames").fetchone()[0] == membership
        assert conn.execute("SELECT COUNT(*) FROM working_days").fetchone()[0] == days
        assert conn.execute("SELECT COUNT(*) FROM settings").fetchone()[0] == settings
        assert conn.execute("SELECT COUNT(*) FROM frame_text").fetchone()[0] == 0
        assert (
            conn.execute("SELECT COUNT(*) FROM segments WHERE merged_text != ''").fetchone()[0] == 0
        )
        after_titles = [
            row[0] for row in conn.execute("SELECT window_title FROM frames").fetchall()
        ]
        assert after_titles == titles
        assert conn.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0] == audit + 1
    finally:
        conn.close()


def test_on_delete_cascade_and_migration_roundtrip(data_dir: Path, key_provider: object) -> None:
    path = data_dir / "watson.db"
    command.upgrade(alembic_config(path, TEST_KEY), "0004_m2b_segments")
    conn = connect(path, TEST_KEY, load_vec=False)
    try:
        day_id = ensure_working_day(conn, "2026-10-31")
        frame_id = _insert_frame(
            conn,
            day_id=day_id,
            captured_at="2026-10-31T09:00:00Z",
            expires_at="2026-11-07T09:00:00Z",
        )
        extra = _insert_frame(
            conn,
            day_id=day_id,
            captured_at="2026-10-31T09:01:00Z",
            expires_at="2026-11-07T09:01:00Z",
        )
        segment_id = _insert_segment(
            conn,
            day_id=day_id,
            frame_id=frame_id,
            started_at="2026-10-31T09:00:00Z",
            expires_at="2026-11-07T09:00:00Z",
        )
        conn.execute(
            """
            INSERT INTO segment_frames (
                id, created_at, updated_at, segment_id, frame_id, captured_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                new_id(),
                "2026-10-31T09:01:00Z",
                "2026-10-31T09:01:00Z",
                segment_id,
                extra,
                "2026-10-31T09:01:00Z",
            ),
        )
        conn.commit()
    finally:
        conn.close()

    assert upgrade_head(path, TEST_KEY) == "0008_m3d_daily_summaries"
    conn = connect(path, TEST_KEY, load_vec=False)
    try:
        actions = _fk_actions(conn)
        assert actions[("frame_text", "frame_id")] == "CASCADE"
        assert actions[("segment_frames", "segment_id")] == "CASCADE"
        assert actions[("segment_frames", "frame_id")] == "CASCADE"
        assert actions[("segments", "start_frame_id")] == "CASCADE"

        conn.execute("DELETE FROM frames WHERE id = ?", (extra,))
        conn.commit()
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM segment_frames WHERE frame_id = ?", (extra,)
            ).fetchone()[0]
            == 0
        )
        assert (
            conn.execute("SELECT COUNT(*) FROM frame_text WHERE frame_id = ?", (extra,)).fetchone()[
                0
            ]
            == 0
        )
        assert (
            conn.execute("SELECT COUNT(*) FROM segments WHERE id = ?", (segment_id,)).fetchone()[0]
            == 1
        )

        conn.execute("DELETE FROM segments WHERE id = ?", (segment_id,))
        conn.commit()
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM segment_frames WHERE segment_id = ?", (segment_id,)
            ).fetchone()[0]
            == 0
        )
        assert (
            conn.execute("SELECT COUNT(*) FROM frames WHERE id = ?", (frame_id,)).fetchone()[0] == 1
        )
    finally:
        conn.close()


def _fk_actions(conn: object) -> dict[tuple[str, str], str]:
    found: dict[tuple[str, str], str] = {}
    for table in ("frame_text", "segments", "segment_frames"):
        for row in conn.execute(f"PRAGMA foreign_key_list({table})"):
            found[(table, str(row["from"]))] = str(row["on_delete"]).upper()
    return found
