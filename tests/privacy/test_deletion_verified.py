"""§11.3: after replay, spool PNGs are gone, every frame is deletion_verified."""

from __future__ import annotations

from pathlib import Path

from tests.replay_support import DAY_ACME_Q4, open_test_db
from watson.pipeline.replay import replay_fixture
from watson.privacy.audit import KIND_DELETED, KIND_INGESTED, KIND_VERIFIED


def test_day_acme_q4_replay_deletes_and_verifies(
    data_dir: Path, key_provider: object
) -> None:
    spool = data_dir / "spool"
    source_pngs = sorted(DAY_ACME_Q4.glob("frames/*/*.png"))
    source_jsons = sorted(DAY_ACME_Q4.glob("frames/*/*.json"))
    assert source_pngs and source_jsons
    source_hashes = {path.name: path.read_bytes() for path in [*source_pngs, *source_jsons]}

    conn = open_test_db(data_dir)
    try:
        result = replay_fixture(DAY_ACME_Q4, conn, instant=True, spool_dir=spool)
        n = result.frame_count
        assert n == len(source_pngs)
        assert result.ingested == n
        assert result.extracted == n
        assert result.deleted == n
        assert result.deletion_verified == n
        assert list(spool.rglob("*.png")) == []
        assert list(spool.rglob("*.json")) == []

        rows = conn.execute(
            "SELECT id, state, deletion_verified FROM frames"
        ).fetchall()
        assert len(rows) == n
        assert all(row["state"] == "deleted" for row in rows)
        assert all(int(row["deletion_verified"]) == 1 for row in rows)

        ingest_n = conn.execute(
            "SELECT COUNT(*) FROM audit_events WHERE kind = ?", (KIND_INGESTED,)
        ).fetchone()[0]
        delete_n = conn.execute(
            "SELECT COUNT(*) FROM audit_events WHERE kind = ?", (KIND_DELETED,)
        ).fetchone()[0]
        verify_n = conn.execute(
            "SELECT COUNT(*) FROM audit_events WHERE kind = ?", (KIND_VERIFIED,)
        ).fetchone()[0]
        assert ingest_n == n
        assert delete_n == n
        assert verify_n == n

        details = [
            str(row[0])
            for row in conn.execute("SELECT detail_json FROM audit_events").fetchall()
        ]
        assert all("ocr" not in item for item in details)
        assert all("window_title" not in item for item in details)
        assert all("text_redacted" not in item for item in details)
    finally:
        conn.close()

    for path in [*source_pngs, *source_jsons]:
        assert path.is_file()
        assert path.read_bytes() == source_hashes[path.name]
