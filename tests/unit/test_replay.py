"""Replay validation, spool permissions, ingest idempotency, and extract stub."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.replay_support import TEST_KEY, open_test_db, write_mini_fixture
from watson.capture.spool import (
    DIR_MODE,
    FILE_MODE,
    SpoolError,
    copy_pairs_to_spool,
    iter_pairs,
)
from watson.cli import main
from watson.pipeline.frames import (
    FIXTURE_TEXT_SOURCE,
    FrameError,
    delete_and_verify,
    ensure_working_day,
    extract_fixture_stub,
    ingest_pair,
)
from watson.pipeline.replay import ReplayError, load_and_validate_manifest, replay_fixture
from watson.pipeline.status import collect_status
from watson.privacy.audit import (
    KIND_DELETE_FAILED,
    KIND_DELETED,
    KIND_INGESTED,
    KIND_VERIFIED,
)


def test_manifest_rejects_sha256_and_dimension_mismatch(tmp_path: Path) -> None:
    fixture = write_mini_fixture(tmp_path)
    manifest_path = fixture / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    frame_id = next(iter(manifest["frames"]))
    manifest["frames"][frame_id]["sha256"] = "00" * 32
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ReplayError, match="sha256"):
        load_and_validate_manifest(fixture)

    fixture2 = write_mini_fixture(tmp_path / "other", name="day_mini2")
    manifest_path = fixture2 / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    frame_id = next(iter(manifest["frames"]))
    manifest["frames"][frame_id]["width"] = 99
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ReplayError, match="dimension"):
        load_and_validate_manifest(fixture2)


def test_sidecar_schema_rejection_names_field_not_value(tmp_path: Path) -> None:
    fixture = write_mini_fixture(tmp_path, frames=1)
    sidecar_path = next(fixture.glob("frames/*/*.json"))
    payload = json.loads(sidecar_path.read_text(encoding="utf-8"))
    payload["capture_mode"] = "not-a-mode"
    sidecar_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ReplayError, match="capture_mode") as raised:
        load_and_validate_manifest(fixture)
    assert payload["ocr"]["lines"][0]["text"] not in str(raised.value)


def test_replay_is_idempotent_and_sets_fixture_only_text(
    data_dir: Path, key_provider: object, tmp_path: Path
) -> None:
    fixture = write_mini_fixture(tmp_path, frames=2)
    spool = data_dir / "spool-replay"
    conn = open_test_db(data_dir)
    try:
        first = replay_fixture(fixture, conn, instant=True, spool_dir=spool)
        assert first.frame_count == 2
        assert first.ingested == 2
        assert first.extracted == 2
        assert first.deletion_verified == 2
        assert list(spool.rglob("*.png")) == []
        assert list(spool.rglob("*.json")) == []
        for path in [*fixture.rglob("*.png"), *fixture.rglob("*.json")]:
            assert path.is_file()

        rows = conn.execute("SELECT id, state, deletion_verified FROM frames").fetchall()
        assert len(rows) == 2
        assert all(row["state"] == "deleted" and row["deletion_verified"] == 1 for row in rows)
        sources = [
            row[0]
            for row in conn.execute("SELECT source FROM frame_text").fetchall()
        ]
        assert sources == [FIXTURE_TEXT_SOURCE, FIXTURE_TEXT_SOURCE]

        second = replay_fixture(fixture, conn, instant=True, spool_dir=spool)
        assert second.ingested == 0
        assert second.skipped_existing == 2
        assert conn.execute("SELECT COUNT(*) FROM frames").fetchone()[0] == 2
        ingest_n = conn.execute(
            "SELECT COUNT(*) FROM audit_events WHERE kind = ?", (KIND_INGESTED,)
        ).fetchone()[0]
        delete_n = conn.execute(
            "SELECT COUNT(*) FROM audit_events WHERE kind = ?", (KIND_DELETED,)
        ).fetchone()[0]
        verify_n = conn.execute(
            "SELECT COUNT(*) FROM audit_events WHERE kind = ?", (KIND_VERIFIED,)
        ).fetchone()[0]
        assert ingest_n == delete_n == verify_n == 2
    finally:
        conn.close()


def test_spool_copy_uses_0700_0600_and_refuses_fixture_dir(tmp_path: Path) -> None:
    fixture = write_mini_fixture(tmp_path, frames=1)
    manifest = json.loads((fixture / "manifest.json").read_text(encoding="utf-8"))
    dest = tmp_path / "spool"
    copy_pairs_to_spool(fixture, manifest["frames"].items(), dest)
    assert dest.stat().st_mode & 0o777 == DIR_MODE
    for path in dest.rglob("*"):
        mode = path.stat().st_mode & 0o777
        assert mode == (DIR_MODE if path.is_dir() else FILE_MODE)
    with pytest.raises(SpoolError, match="fixture"):
        copy_pairs_to_spool(fixture, manifest["frames"].items(), fixture)


def test_cli_replay_and_status(
    data_dir: Path,
    key_provider: object,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    fixture = write_mini_fixture(tmp_path, frames=1)
    assert main(["replay", str(fixture), "--instant"]) == 0
    out = capsys.readouterr().out
    assert "replay: day_mini" in out
    assert "deletion_verified: 1" in out
    assert TEST_KEY.hex() not in out

    assert main(["status"]) == 0
    status = capsys.readouterr().out
    assert "frames.deleted: 1" in status
    assert "jobs.completed: 1" in status
    assert "last_replay.fixture: day_mini" in status
    assert TEST_KEY.hex() not in status

    conn = open_test_db(data_dir)
    try:
        report = collect_status(conn)
        assert report.frames_by_state["deleted"] == 1
        assert report.last_replay is not None
        assert report.last_replay["frame_count"] == 1
    finally:
        conn.close()


def test_delete_failure_stays_unverified_then_retry(
    data_dir: Path, key_provider: object, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    planted = "PLANTED_DELETE_FAIL_OCR_MUST_NOT_REACH_AUDIT"
    fixture = write_mini_fixture(tmp_path, frames=1, planted=planted)
    spool = tmp_path / "spool"
    conn = open_test_db(data_dir)
    try:
        manifest = load_and_validate_manifest(fixture)
        frames_map = manifest["frames"]
        assert isinstance(frames_map, dict)
        copy_pairs_to_spool(fixture, frames_map.items(), spool)
        pair = iter_pairs(spool)[0]
        day_id = ensure_working_day(conn, str(manifest["date"]))
        ingest_pair(conn, pair, working_day_id=day_id)
        payload = {
            "frame_id": pair.frame_id,
            "png_path": str(pair.png_path),
            "sidecar_path": str(pair.sidecar_path),
            "fixture_only": True,
        }
        original_unlink = Path.unlink

        def permission_unlink(self: Path, *args: object, **kwargs: object) -> None:
            raise PermissionError("denied")

        monkeypatch.setattr("pathlib.Path.unlink", permission_unlink)
        with pytest.raises(FrameError, match="spool delete failed"):
            extract_fixture_stub(conn, payload, fixture_dir=fixture)
        row = conn.execute(
            "SELECT state, deletion_verified, failure_count FROM frames WHERE id = ?",
            (pair.frame_id,),
        ).fetchone()
        assert row is not None
        assert row["state"] == "extracted"
        assert int(row["deletion_verified"]) == 0
        assert int(row["failure_count"]) == 1
        assert pair.png_path.is_file()
        assert pair.sidecar_path.is_file()

        def reappear_unlink(self: Path, *args: object, **kwargs: object) -> None:
            blob = Path(self).read_bytes()
            original_unlink(self, *args, **kwargs)
            Path(self).write_bytes(blob)

        monkeypatch.setattr("pathlib.Path.unlink", reappear_unlink)
        with pytest.raises(FrameError, match="still present"):
            delete_and_verify(
                conn,
                pair.frame_id,
                pair.png_path,
                fixture_dir=fixture,
                sidecar_path=pair.sidecar_path,
            )
        row = conn.execute(
            "SELECT state, deletion_verified, failure_count FROM frames WHERE id = ?",
            (pair.frame_id,),
        ).fetchone()
        assert row is not None
        assert row["state"] == "extracted"
        assert int(row["deletion_verified"]) == 0
        assert int(row["failure_count"]) == 2

        fails = conn.execute(
            "SELECT detail_json FROM audit_events WHERE kind = ? ORDER BY created_at",
            (KIND_DELETE_FAILED,),
        ).fetchall()
        assert len(fails) == 2
        details = [str(item[0]) for item in fails]
        assert "PermissionError" in details[0]
        assert "still_present" in details[1]
        assert all(planted not in item for item in details)
        assert all("ocr" not in item for item in details)

        monkeypatch.setattr("pathlib.Path.unlink", original_unlink)
        delete_and_verify(
            conn,
            pair.frame_id,
            pair.png_path,
            fixture_dir=fixture,
            sidecar_path=pair.sidecar_path,
        )
        done = conn.execute(
            "SELECT state, deletion_verified FROM frames WHERE id = ?",
            (pair.frame_id,),
        ).fetchone()
        assert done is not None
        assert done["state"] == "deleted"
        assert int(done["deletion_verified"]) == 1
        assert not pair.png_path.exists()
        assert not pair.sidecar_path.exists()
        assert next(fixture.rglob("*.png")).is_file()
        assert next(fixture.rglob("*.json")).is_file()
    finally:
        conn.close()


def test_speed_paces_between_frames(
    data_dir: Path, key_provider: object, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    slept: list[float] = []
    monkeypatch.setattr("watson.pipeline.replay.time.sleep", slept.append)
    fixture = write_mini_fixture(tmp_path, frames=2)
    conn = open_test_db(data_dir)
    try:
        replay_fixture(fixture, conn, instant=False, speed=10.0, spool_dir=tmp_path / "spool")
    finally:
        conn.close()
    assert slept == [6.0]  # 09:02 - 09:01 = 60s / 10
