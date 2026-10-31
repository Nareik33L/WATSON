"""Replay day_fragmented_multi: deletion verified, excluded window absent."""

from __future__ import annotations

from pathlib import Path

import yaml

from tests.replay_support import open_test_db
from watson.pipeline.replay import replay_fixture
from watson.privacy.audit import KIND_DELETED, KIND_INGESTED, KIND_VERIFIED

REPO = Path(__file__).resolve().parents[2]
FIXTURE = REPO / "tests" / "fixtures" / "day_fragmented_multi"
VAULT_BUNDLE = "com.cedar.vault"
VAULT_APP = "Cedar Vault"


def test_fragmented_replay_verifies_deletion_and_drops_excluded(
    data_dir: Path, key_provider: object
) -> None:
    truth = yaml.safe_load((FIXTURE / "truth.yaml").read_text(encoding="utf-8"))
    assert truth["skipped"]
    assert {row["bundle_id"] for row in truth["skipped"]} == {VAULT_BUNDLE}

    source_pngs = sorted(FIXTURE.glob("frames/*/*.png"))
    source_jsons = sorted(FIXTURE.glob("frames/*/*.json"))
    assert source_pngs and source_jsons
    source_hashes = {path.name: path.read_bytes() for path in [*source_pngs, *source_jsons]}

    spool = data_dir / "spool"
    conn = open_test_db(data_dir)
    try:
        result = replay_fixture(FIXTURE, conn, instant=True, spool_dir=spool)
        n = result.frame_count
        assert n == len(source_pngs)
        assert result.ingested == n
        assert result.extracted == n
        assert result.deleted == n
        assert result.deletion_verified == n
        assert list(spool.rglob("*.png")) == []
        assert list(spool.rglob("*.json")) == []

        rows = conn.execute(
            "SELECT id, bundle_id, app_name, state, deletion_verified FROM frames"
        ).fetchall()
        assert len(rows) == n
        assert all(row["state"] == "deleted" for row in rows)
        assert all(int(row["deletion_verified"]) == 1 for row in rows)
        assert all(row["bundle_id"] != VAULT_BUNDLE for row in rows)
        assert all(row["app_name"] != VAULT_APP for row in rows)

        text_apps = [
            str(row[0])
            for row in conn.execute(
                """
                SELECT f.bundle_id FROM frame_text t
                JOIN frames f ON f.id = t.frame_id
                """
            ).fetchall()
        ]
        assert VAULT_BUNDLE not in text_apps

        ingest_n = conn.execute(
            "SELECT COUNT(*) FROM audit_events WHERE kind = ?", (KIND_INGESTED,)
        ).fetchone()[0]
        delete_n = conn.execute(
            "SELECT COUNT(*) FROM audit_events WHERE kind = ?", (KIND_DELETED,)
        ).fetchone()[0]
        verify_n = conn.execute(
            "SELECT COUNT(*) FROM audit_events WHERE kind = ?", (KIND_VERIFIED,)
        ).fetchone()[0]
        assert ingest_n == delete_n == verify_n == n
    finally:
        conn.close()

    for path in [*source_pngs, *source_jsons]:
        assert path.is_file()
        assert path.read_bytes() == source_hashes[path.name]
