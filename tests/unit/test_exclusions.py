"""Python ingest guard drops excluded frames before they reach the DB."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.replay_support import open_test_db, write_mini_fixture
from watson.capture.exclusions import (
    REASON_EXCLUDED_APP,
    REASON_EXCLUDED_TITLE,
    REASON_PRIVATE,
    exclusion_reason,
)
from watson.capture.spool import copy_pairs_to_spool, iter_pairs
from watson.config.schema import DEFAULT_EXCLUDED_BUNDLE_IDS, ExclusionsConfig
from watson.pipeline.frames import FrameError, ensure_working_day, ingest_pair
from watson.pipeline.replay import load_and_validate_manifest, replay_fixture
from watson.pipeline.status import collect_status
from watson.privacy.audit import (
    KIND_DELETE_FAILED,
    KIND_INGESTED,
    KIND_SKIPPED,
    AuditError,
    record_audit,
)


def test_exclusion_reason_matches_bundle_title_and_private() -> None:
    rules = ExclusionsConfig()
    assert (
        exclusion_reason(
            {
                "bundle_id": "com.apple.Passwords",
                "app_name": "Passwords",
                "window_title": "Inbox",
                "is_private_browsing": False,
            },
            rules,
        )
        == REASON_EXCLUDED_APP
    )
    assert (
        exclusion_reason(
            {
                "bundle_id": "com.cedar.mail",
                "app_name": "Cedar Mail",
                "window_title": "Sign in to Harbor",
                "is_private_browsing": False,
            },
            rules,
        )
        == REASON_EXCLUDED_TITLE
    )
    assert (
        exclusion_reason(
            {
                "bundle_id": "com.cedar.mail",
                "app_name": "Cedar Mail",
                "window_title": "Inbox",
                "is_private_browsing": True,
            },
            rules,
        )
        == REASON_PRIVATE
    )
    assert (
        exclusion_reason(
            {
                "bundle_id": "com.cedar.mail",
                "app_name": "Cedar Mail",
                "window_title": "Inbox",
                "is_private_browsing": False,
            },
            rules,
        )
        is None
    )
    assert "com.apple.Passwords" in DEFAULT_EXCLUDED_BUNDLE_IDS
    extras = ExclusionsConfig(bundle_ids=("com.example.secret",))
    assert "com.example.secret" in extras.bundle_ids
    assert "com.apple.Passwords" in extras.bundle_ids
    assert extras.bundle_ids != ("com.example.secret",)
    override = ExclusionsConfig(allowed_bundle_ids=("com.apple.Passwords",))
    assert "com.apple.Passwords" not in override.bundle_ids
    assert "com.1password.1password" in override.bundle_ids
    padded = ExclusionsConfig(bundle_ids=("com.example.secret  ",))
    assert "com.example.secret" in padded.bundle_ids
    assert "com.example.secret  " not in padded.bundle_ids
    assert (
        exclusion_reason(
            {
                "bundle_id": "com.apple.Passwords ",
                "app_name": "Passwords",
                "window_title": "Inbox",
                "is_private_browsing": False,
            },
            extras,
        )
        == REASON_EXCLUDED_APP
    )


def test_injected_excluded_frame_is_dropped_not_stored(
    data_dir: Path, key_provider: object, tmp_path: Path
) -> None:
    planted = "PLANTED_EXCLUDED_VAULT_OCR_MUST_NEVER_LAND"
    fixture = write_mini_fixture(tmp_path, frames=2, planted="PLANTED_NORMAL_FRAME_OCR")
    sidecars = sorted(fixture.glob("frames/*/*.json"))
    payload = json.loads(sidecars[-1].read_text(encoding="utf-8"))
    payload["bundle_id"] = "com.apple.Passwords"
    payload["app_name"] = "Passwords"
    payload["window_title"] = "Passwords — Personal vault"
    payload["is_private_browsing"] = True
    payload["ocr"]["lines"][0]["text"] = planted
    sidecars[-1].write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    spool = data_dir / "spool-excluded"
    conn = open_test_db(data_dir)
    try:
        result = replay_fixture(fixture, conn, instant=True, spool_dir=spool)
        assert result.frame_count == 2
        assert result.ingested == 1
        assert result.extracted == 1
        assert result.deleted == 1
        assert result.deletion_verified == 1
        assert result.skipped_excluded == 1
        assert list(spool.rglob("*.png")) == []
        assert list(spool.rglob("*.json")) == []

        rows = conn.execute("SELECT id, bundle_id, app_name, window_title FROM frames").fetchall()
        assert len(rows) == 1
        assert rows[0]["bundle_id"] != "com.apple.Passwords"
        assert rows[0]["app_name"] != "Passwords"
        dumped = " ".join(str(item) for row in rows for item in row)
        assert "com.apple.Passwords" not in dumped
        assert planted not in dumped
        assert "Personal vault" not in dumped

        texts = [
            str(row[0]) for row in conn.execute("SELECT text_redacted FROM frame_text").fetchall()
        ]
        assert all(planted not in text for text in texts)
        assert all("Passwords" not in text for text in texts)

        skip_n = conn.execute(
            "SELECT COUNT(*) FROM audit_events WHERE kind = ?", (KIND_SKIPPED,)
        ).fetchone()[0]
        ingest_n = conn.execute(
            "SELECT COUNT(*) FROM audit_events WHERE kind = ?", (KIND_INGESTED,)
        ).fetchone()[0]
        assert skip_n == 1
        assert ingest_n == 1
        details = [
            row[0]
            for row in conn.execute(
                "SELECT detail_json FROM audit_events WHERE kind = ?", (KIND_SKIPPED,)
            ).fetchall()
        ]
        assert all("com.apple.Passwords" not in item for item in details)
        assert all("Passwords" not in item for item in details)
        assert all(planted not in item for item in details)
        assert all('"reason": "excluded_app"' in item for item in details)
        assert all("bundle_id" not in item for item in details)
        assert all("app_name" not in item for item in details)
    finally:
        conn.close()


def test_skipped_audit_rejects_bundle_id(data_dir: Path, key_provider: object) -> None:
    conn = open_test_db(data_dir)
    try:
        with pytest.raises(AuditError, match="content fields"):
            record_audit(
                conn,
                kind=KIND_SKIPPED,
                detail={"reason": "excluded_app", "bundle_id": "com.apple.Passwords"},
            )
    finally:
        conn.close()


def test_excluded_unlink_failure_audits_and_status(
    data_dir: Path, key_provider: object, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    planted = "PLANTED_EXCLUDED_UNLINK_FAIL_MUST_NOT_REACH_AUDIT"
    fixture = write_mini_fixture(tmp_path, frames=1, planted=planted)
    sidecar_path = next(fixture.glob("frames/*/*.json"))
    payload = json.loads(sidecar_path.read_text(encoding="utf-8"))
    payload["bundle_id"] = "com.apple.Passwords"
    payload["app_name"] = "Passwords"
    payload["window_title"] = "Passwords — Personal vault"
    sidecar_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    spool = tmp_path / "spool"
    conn = open_test_db(data_dir)
    try:
        manifest = load_and_validate_manifest(fixture)
        frames_map = manifest["frames"]
        assert isinstance(frames_map, dict)
        copy_pairs_to_spool(fixture, frames_map.items(), spool)
        pair = iter_pairs(spool)[0]
        day_id = ensure_working_day(conn, str(manifest["date"]))

        def permission_unlink(self: Path, *args: object, **kwargs: object) -> None:
            raise PermissionError("denied")

        monkeypatch.setattr("pathlib.Path.unlink", permission_unlink)
        with pytest.raises(FrameError, match="excluded spool delete failed"):
            ingest_pair(conn, pair, working_day_id=day_id)
        assert pair.png_path.is_file()
        assert conn.execute("SELECT COUNT(*) FROM frames").fetchone()[0] == 0
        fails = conn.execute(
            "SELECT detail_json FROM audit_events WHERE kind = ?",
            (KIND_DELETE_FAILED,),
        ).fetchall()
        assert len(fails) == 1
        detail = str(fails[0][0])
        assert "PermissionError" in detail
        assert "excluded_app" in detail
        assert "com.apple.Passwords" not in detail
        assert "Passwords" not in detail
        assert planted not in detail
        assert "bundle_id" not in detail
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM audit_events WHERE kind = ?", (KIND_SKIPPED,)
            ).fetchone()[0]
            == 0
        )
        report = collect_status(conn)
        assert report.deletion_failed_count == 1
        assert "deletion.failed: 1" in "\n".join(report.lines())
        dumped = json.dumps(report.as_dict())
        assert planted not in dumped
        assert "com.apple.Passwords" not in dumped
    finally:
        conn.close()
