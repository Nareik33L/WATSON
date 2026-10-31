"""Bounded extract backlog drain does not block ingest."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.replay_support import open_test_db, write_mini_fixture
from watson.capture.spool import copy_pairs_to_spool, iter_pairs
from watson.pipeline.backlog import backlog_depth, drain_backlog
from watson.pipeline.frames import ensure_working_day, ingest_pair
from watson.pipeline.replay import load_and_validate_manifest
from watson.pipeline.status import collect_status


def test_drain_backlog_is_bounded_and_ingest_still_works(
    data_dir: Path, key_provider: object, tmp_path: Path
) -> None:
    fixture = write_mini_fixture(tmp_path, frames=5)
    spool = tmp_path / "spool"
    conn = open_test_db(data_dir)
    try:
        manifest = load_and_validate_manifest(fixture)
        frames_map = manifest["frames"]
        assert isinstance(frames_map, dict)
        copy_pairs_to_spool(fixture, frames_map.items(), spool)
        pairs = iter_pairs(spool)
        day_id = ensure_working_day(conn, str(manifest["date"]))
        for pair in pairs:
            assert ingest_pair(conn, pair, working_day_id=day_id) == "ingested"

        assert backlog_depth(conn) == 5
        report = collect_status(conn)
        assert report.backlog_depth == 5
        assert report.capture_state == "backlog"
        assert "backlog.depth: 5" in "\n".join(report.lines())

        first = drain_backlog(conn, limit=2, fixture_dir=fixture)
        assert first == 2
        assert backlog_depth(conn) == 3
        assert collect_status(conn).backlog_depth == 3

        extra = write_mini_fixture(tmp_path / "extra", name="day_extra", frames=1)
        extra_spool = tmp_path / "spool-extra"
        extra_manifest = load_and_validate_manifest(extra)
        extra_frames = extra_manifest["frames"]
        assert isinstance(extra_frames, dict)
        copy_pairs_to_spool(extra, extra_frames.items(), extra_spool)
        extra_pair = iter_pairs(extra_spool)[0]
        assert ingest_pair(conn, extra_pair, working_day_id=day_id) == "ingested"
        assert backlog_depth(conn) == 4

        rest = drain_backlog(conn, limit=10, fixture_dir=fixture)
        # The extra pair lives in another spool; extract of those jobs
        # uses the stored sidecar path, so they complete too.
        assert rest == 4
        assert backlog_depth(conn) == 0
        idle = collect_status(conn)
        assert idle.backlog_depth == 0
        assert idle.capture_state == "idle"
    finally:
        conn.close()


def test_drain_backlog_rejects_zero_limit(data_dir: Path, key_provider: object) -> None:
    conn = open_test_db(data_dir)
    try:
        with pytest.raises(ValueError, match="limit"):
            drain_backlog(conn, limit=0)
    finally:
        conn.close()
