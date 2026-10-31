"""Spool sweeper: orphan/stale cleanup, symlink refusal, unlink-failure audit."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from tests.replay_support import open_test_db, write_mini_fixture, write_png
from watson.capture.spool import copy_pairs_to_spool, iter_pairs
from watson.memory.ids import new_id
from watson.pipeline.frames import ensure_working_day
from watson.pipeline.replay import load_and_validate_manifest
from watson.privacy.audit import KIND_DELETE_FAILED, KIND_SPOOL_SWEPT
from watson.privacy.spoolsweep import (
    SpoolSweepError,
    refuse_spool_path,
    sweep_spool,
)


def _write_pair(spool: Path, frame_id: str, *, mtime: float | None = None) -> tuple[Path, Path]:
    day = spool / "2026-10-31"
    day.mkdir(parents=True, exist_ok=True)
    png = day / f"{frame_id}.png"
    sidecar = day / f"{frame_id}.json"
    write_png(png, 2, 2)
    sidecar.write_text("{}", encoding="utf-8")
    if mtime is not None:
        os.utime(png, (mtime, mtime))
        os.utime(sidecar, (mtime, mtime))
    return png, sidecar


def _write_png_only(spool: Path, frame_id: str, *, mtime: float | None = None) -> Path:
    day = spool / "2026-10-31"
    day.mkdir(parents=True, exist_ok=True)
    png = day / f"{frame_id}.png"
    write_png(png, 2, 2)
    if mtime is not None:
        os.utime(png, (mtime, mtime))
    return png


def _set_mtime(paths: list[Path], mtime: float) -> None:
    for path in paths:
        os.utime(path, (mtime, mtime))


def test_sweeper_refuses_symlink_and_outside_dir(tmp_path: Path) -> None:
    spool = tmp_path / "spool"
    spool.mkdir()
    outside = tmp_path / "outside.png"
    write_png(outside, 2, 2)
    linked = spool / "escape.png"
    linked.symlink_to(outside)
    with pytest.raises(SpoolSweepError, match="symlink"):
        refuse_spool_path(linked, spool)
    assert outside.is_file()
    assert linked.is_symlink()

    escaped = (spool / ".." / "outside.png").resolve()
    with pytest.raises(SpoolSweepError, match="outside spool"):
        refuse_spool_path(escaped, spool)
    assert escaped.is_file()


def test_sweep_skips_symlink_and_keeps_target(
    data_dir: Path, key_provider: object, tmp_path: Path
) -> None:
    spool = tmp_path / "spool"
    spool.mkdir()
    outside = tmp_path / "secret.png"
    write_png(outside, 2, 2)
    day = spool / "2026-10-31"
    day.mkdir()
    link = day / f"{new_id()}.png"
    link.symlink_to(outside)
    conn = open_test_db(data_dir)
    try:
        result = sweep_spool(conn, spool)
        assert result.refused == 1
        assert result.png == 0
        assert link.is_symlink()
        assert outside.is_file()
    finally:
        conn.close()


def test_sweep_removes_orphan_and_stale_live_frames(
    data_dir: Path, key_provider: object, tmp_path: Path
) -> None:
    now = datetime(2026, 10, 31, 18, 0, tzinfo=UTC)
    spool = tmp_path / "spool"
    orphan_id = str(new_id())
    stale_id = str(new_id())
    live_id = str(new_id())
    old = (now - timedelta(hours=24)).timestamp()
    fresh = (now - timedelta(hours=1)).timestamp()
    orphan_png, orphan_json = _write_pair(spool, orphan_id, mtime=fresh)
    stale_png, stale_json = _write_pair(spool, stale_id, mtime=old)
    live_png, live_json = _write_pair(spool, live_id, mtime=fresh)

    conn = open_test_db(data_dir)
    try:
        day_id = ensure_working_day(conn, "2026-10-31")
        for frame_id, captured in (
            (stale_id, "2026-10-30T18:00:00Z"),
            (live_id, "2026-10-31T17:00:00Z"),
        ):
            conn.execute(
                """
                INSERT INTO frames (
                    id, created_at, updated_at, working_day_id, captured_at,
                    bundle_id, app_name, window_title_hash, window_title,
                    url_domain, phash, state, failure_count, deleted_at, deletion_verified
                ) VALUES (?, ?, ?, ?, ?, 'com.lumen.mail', 'Lumen Mail', NULL, 'Inbox',
                          NULL, 'abcd', 'captured', 0, NULL, 0)
                """,
                (frame_id, captured, captured, day_id, captured),
            )
        conn.commit()

        result = sweep_spool(conn, spool, now=now, hard_limit_hours=24)
        assert result.orphaned == 0
        assert result.stale == 1
        assert result.unprocessed == 1
        assert result.png == 1
        assert result.sidecar == 1
        assert orphan_png.is_file()
        assert orphan_json.is_file()
        assert not stale_png.exists()
        assert not stale_json.exists()
        assert live_png.is_file()
        assert live_json.is_file()
        stale_state = conn.execute("SELECT state FROM frames WHERE id = ?", (stale_id,)).fetchone()[
            0
        ]
        live_state = conn.execute("SELECT state FROM frames WHERE id = ?", (live_id,)).fetchone()[0]
        assert stale_state == "deleted_unprocessed"
        assert live_state == "captured"
        again = sweep_spool(conn, spool, now=now, hard_limit_hours=24)
        assert again.png == 0
        assert again.orphaned == 0
        assert again.stale == 0
    finally:
        conn.close()


def test_sweep_keeps_fresh_uningested_pairs_then_removes_them_at_24h(
    data_dir: Path, key_provider: object, tmp_path: Path
) -> None:
    now = datetime(2026, 10, 31, 18, 0, tzinfo=UTC)
    fresh = now.timestamp()
    aged = (now - timedelta(hours=24)).timestamp()
    spool = tmp_path / "spool"
    no_row_id = str(new_id())
    half_id = str(new_id())
    no_row_png, no_row_json = _write_pair(spool, no_row_id, mtime=fresh)
    half_png = _write_png_only(spool, half_id, mtime=fresh)

    fixture = write_mini_fixture(tmp_path, frames=1)
    manifest = load_and_validate_manifest(fixture)
    frames_map = manifest["frames"]
    assert isinstance(frames_map, dict)
    copy_pairs_to_spool(fixture, frames_map.items(), spool)
    fixture_paths: list[tuple[Path, Path]] = []
    for frame_id in frames_map:
        png = spool / "2026-10-31" / f"{frame_id}.png"
        sidecar = spool / "2026-10-31" / f"{frame_id}.json"
        _set_mtime([png, sidecar], fresh)
        fixture_paths.append((png, sidecar))

    conn = open_test_db(data_dir)
    try:
        result = sweep_spool(conn, spool, now=now, hard_limit_hours=24)
        assert result.orphaned == 0
        assert result.png == 0
        assert result.sidecar == 0
        assert no_row_png.is_file()
        assert no_row_json.is_file()
        assert half_png.is_file()
        for png, sidecar in fixture_paths:
            assert png.is_file()
            assert sidecar.is_file()

        _set_mtime([no_row_png, no_row_json, half_png], aged)
        for png, sidecar in fixture_paths:
            _set_mtime([png, sidecar], aged)

        aged_result = sweep_spool(conn, spool, now=now, hard_limit_hours=24)
        assert aged_result.orphaned == 3
        assert aged_result.png == 3
        assert aged_result.sidecar == 2
        assert not no_row_png.exists()
        assert not no_row_json.exists()
        assert not half_png.exists()
        for png, sidecar in fixture_paths:
            assert not png.exists()
            assert not sidecar.exists()
    finally:
        conn.close()


def test_sweep_orphan_boundary_just_under_and_just_over_24h(
    data_dir: Path, key_provider: object, tmp_path: Path
) -> None:
    now = datetime(2026, 10, 31, 18, 0, tzinfo=UTC)
    just_under = (now - timedelta(hours=24) + timedelta(seconds=1)).timestamp()
    just_over = (now - timedelta(hours=24) - timedelta(seconds=1)).timestamp()
    spool = tmp_path / "spool"
    keep_png, keep_json = _write_pair(spool, str(new_id()), mtime=just_under)
    drop_png, drop_json = _write_pair(spool, str(new_id()), mtime=just_over)

    conn = open_test_db(data_dir)
    try:
        result = sweep_spool(conn, spool, now=now, hard_limit_hours=24)
        assert result.orphaned == 1
        assert result.png == 1
        assert result.sidecar == 1
        assert keep_png.is_file()
        assert keep_json.is_file()
        assert not drop_png.exists()
        assert not drop_json.exists()
    finally:
        conn.close()


def test_unlink_failure_uses_frame_deletion_failed(
    data_dir: Path, key_provider: object, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = write_mini_fixture(tmp_path, frames=1)
    spool = tmp_path / "spool"
    conn = open_test_db(data_dir)
    try:
        manifest = load_and_validate_manifest(fixture)
        frames_map = manifest["frames"]
        assert isinstance(frames_map, dict)
        copy_pairs_to_spool(fixture, frames_map.items(), spool)
        pair = iter_pairs(spool)[0]
        aged = (datetime(2026, 10, 31, 18, 0, tzinfo=UTC) - timedelta(hours=24)).timestamp()
        _set_mtime([pair.png_path, pair.sidecar_path], aged)

        def boom(self: Path, *args: object, **kwargs: object) -> None:
            raise PermissionError("denied")

        monkeypatch.setattr("pathlib.Path.unlink", boom)
        with pytest.raises(SpoolSweepError, match="spool delete failed"):
            sweep_spool(conn, spool, now=datetime(2026, 10, 31, 18, 0, tzinfo=UTC))
        row = conn.execute(
            "SELECT detail_json FROM audit_events WHERE kind = ?",
            (KIND_DELETE_FAILED,),
        ).fetchone()
        assert row is not None
        assert '"error": "permission"' in str(row[0])
        assert "PermissionError" not in str(row[0])
        assert pair.png_path.is_file() or pair.png_path.is_symlink()
        swept = conn.execute(
            "SELECT COUNT(*) FROM audit_events WHERE kind = ?", (KIND_SPOOL_SWEPT,)
        ).fetchone()[0]
        assert swept == 0
    finally:
        conn.close()
