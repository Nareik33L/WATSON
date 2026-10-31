"""Replay day_fragmented_multi: excluded vault frames never stored."""

from __future__ import annotations

import io
import json
import logging
from pathlib import Path

import yaml

from tests.replay_support import open_test_db
from watson.pipeline.replay import replay_fixture
from watson.privacy.audit import KIND_DELETED, KIND_INGESTED, KIND_SKIPPED, KIND_VERIFIED
from watson.privacy.logging import configure_logging

REPO = Path(__file__).resolve().parents[2]
FIXTURE = REPO / "tests" / "fixtures" / "day_fragmented_multi"
VAULT_BUNDLE = "com.apple.Passwords"
VAULT_APP = "Passwords"
VAULT_TITLE = "Passwords — Personal vault"
VAULT_OCR = "Personal vault unlock"


def test_fragmented_replay_verifies_deletion_and_drops_excluded(
    data_dir: Path, key_provider: object
) -> None:
    truth = yaml.safe_load((FIXTURE / "truth.yaml").read_text(encoding="utf-8"))
    skipped = truth["skipped"]
    assert skipped
    assert {row["bundle_id"] for row in skipped} == {VAULT_BUNDLE}
    skipped_ids = {row["frame_id"] for row in skipped}

    source_pngs = sorted(FIXTURE.glob("frames/*/*.png"))
    source_jsons = sorted(FIXTURE.glob("frames/*/*.json"))
    assert source_pngs and source_jsons
    source_hashes = {path.name: path.read_bytes() for path in [*source_pngs, *source_jsons]}
    excluded_sidecars = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in source_jsons
        if path.stem in skipped_ids
    ]
    assert excluded_sidecars
    assert all(item["bundle_id"] == VAULT_BUNDLE for item in excluded_sidecars)
    assert all(item["is_private_browsing"] is True for item in excluded_sidecars)

    spool = data_dir / "spool"
    buf = io.StringIO()
    configure_logging(level=logging.DEBUG, stream=buf)
    conn = open_test_db(data_dir)
    try:
        result = replay_fixture(FIXTURE, conn, instant=True, spool_dir=spool)
        n = result.frame_count
        work = n - len(skipped)
        assert n == len(source_pngs)
        assert result.skipped_excluded == len(skipped)
        assert result.ingested == work
        assert result.extracted == work
        assert result.deleted == work
        assert result.deletion_verified == work
        assert list(spool.rglob("*.png")) == []
        assert list(spool.rglob("*.json")) == []

        rows = conn.execute(
            "SELECT id, bundle_id, app_name, window_title, state, deletion_verified FROM frames"
        ).fetchall()
        assert len(rows) == work
        assert all(row["state"] == "deleted" for row in rows)
        assert all(int(row["deletion_verified"]) == 1 for row in rows)
        dumped = "\n".join(str(item) for row in rows for item in row)
        assert VAULT_BUNDLE not in dumped
        assert VAULT_APP not in dumped
        assert VAULT_TITLE not in dumped
        assert VAULT_OCR not in dumped
        assert all(row["id"] not in skipped_ids for row in rows)

        text_rows = conn.execute(
            """
            SELECT t.text_redacted, f.bundle_id, f.app_name, f.window_title
            FROM frame_text t
            JOIN frames f ON f.id = t.frame_id
            """
        ).fetchall()
        text_blob = "\n".join(str(item) for row in text_rows for item in row)
        assert VAULT_BUNDLE not in text_blob
        assert VAULT_OCR not in text_blob
        assert VAULT_TITLE not in text_blob

        ingest_n = conn.execute(
            "SELECT COUNT(*) FROM audit_events WHERE kind = ?", (KIND_INGESTED,)
        ).fetchone()[0]
        delete_n = conn.execute(
            "SELECT COUNT(*) FROM audit_events WHERE kind = ?", (KIND_DELETED,)
        ).fetchone()[0]
        verify_n = conn.execute(
            "SELECT COUNT(*) FROM audit_events WHERE kind = ?", (KIND_VERIFIED,)
        ).fetchone()[0]
        skip_n = conn.execute(
            "SELECT COUNT(*) FROM audit_events WHERE kind = ?", (KIND_SKIPPED,)
        ).fetchone()[0]
        assert ingest_n == delete_n == verify_n == work
        assert skip_n == len(skipped)
        audit_blob = "\n".join(
            str(row[0]) for row in conn.execute("SELECT detail_json FROM audit_events").fetchall()
        )
        assert VAULT_BUNDLE not in audit_blob
        assert VAULT_TITLE not in audit_blob
        assert VAULT_OCR not in audit_blob
        assert VAULT_APP not in audit_blob
    finally:
        conn.close()

    logs = buf.getvalue()
    assert VAULT_BUNDLE not in logs
    assert VAULT_TITLE not in logs
    assert VAULT_OCR not in logs
    assert "Unlock personal vault" not in logs

    for path in [*source_pngs, *source_jsons]:
        assert path.is_file()
        assert path.read_bytes() == source_hashes[path.name]
