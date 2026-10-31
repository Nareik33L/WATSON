"""Python ingest guard drops excluded frames before they reach the DB."""

from __future__ import annotations

import json
from pathlib import Path

from tests.replay_support import open_test_db, write_mini_fixture
from watson.capture.exclusions import (
    REASON_EXCLUDED_APP,
    REASON_EXCLUDED_TITLE,
    REASON_PRIVATE,
    exclusion_reason,
)
from watson.config.schema import DEFAULT_EXCLUDED_BUNDLE_IDS, ExclusionsConfig
from watson.pipeline.replay import replay_fixture
from watson.privacy.audit import KIND_INGESTED, KIND_SKIPPED


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
    finally:
        conn.close()
