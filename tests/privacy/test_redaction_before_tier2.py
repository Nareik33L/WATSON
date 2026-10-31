"""§5.3 / §11.3: secrets never reach frame_text or the audit trail."""

from __future__ import annotations

from pathlib import Path

from tests.replay_support import open_test_db, write_mini_fixture
from watson.pipeline.redaction import (
    PLACEHOLDER_CARD,
    PLACEHOLDER_EMAIL,
    PLACEHOLDER_IBAN,
    PLACEHOLDER_OTP,
    PLACEHOLDER_SECRET,
)
from watson.pipeline.replay import replay_fixture
from watson.privacy.audit import KIND_DEDUPED

PLANTED_CARD = "4111111111111111"
PLANTED_EMAIL = "secret.agent@vault.example"
PLANTED_PASSWORD = "hunter2-not-for-storage"
PLANTED_KEY = "sk-abcdefghijklmnopqrstuvwxyz123456"


def test_planted_secrets_are_redacted_before_frame_text(
    data_dir: Path, key_provider: object, tmp_path: Path
) -> None:
    planted = (
        f"From {PLANTED_EMAIL} card {PLANTED_CARD} "
        f"Password: {PLANTED_PASSWORD} token {PLANTED_KEY}"
    )
    fixture = write_mini_fixture(tmp_path, frames=1, planted=planted)
    spool = data_dir / "spool-redact"
    conn = open_test_db(data_dir)
    try:
        result = replay_fixture(fixture, conn, instant=True, spool_dir=spool)
        assert result.extracted == 1
        assert result.deletion_verified == 1
        assert list(spool.rglob("*.png")) == []

        text = str(conn.execute("SELECT text_redacted FROM frame_text").fetchone()[0])
        assert PLANTED_CARD not in text
        assert PLANTED_EMAIL not in text
        assert PLANTED_PASSWORD not in text
        assert PLANTED_KEY not in text
        assert PLACEHOLDER_CARD in text
        assert PLACEHOLDER_EMAIL in text
        assert PLACEHOLDER_SECRET in text

        audit_blob = "\n".join(
            str(row[0]) for row in conn.execute("SELECT detail_json FROM audit_events").fetchall()
        )
        assert PLANTED_CARD not in audit_blob
        assert PLANTED_EMAIL not in audit_blob
        assert PLANTED_PASSWORD not in audit_blob
        assert PLANTED_KEY not in audit_blob
        assert "text_redacted" not in audit_blob
    finally:
        conn.close()

    db_bytes = (data_dir / "watson.db").read_bytes()
    assert PLANTED_CARD.encode() not in db_bytes
    assert PLANTED_EMAIL.encode() not in db_bytes
    assert PLANTED_PASSWORD.encode() not in db_bytes
    assert PLANTED_KEY.encode() not in db_bytes


def _table_blob(conn: object, table: str) -> str:
    if table not in {"jobs", "audit_events", "frames", "working_days", "frame_text", "settings"}:
        raise ValueError("unknown table")
    rows = conn.execute("SELECT * FROM " + table).fetchall()
    return "\n".join(str(item) for row in rows for item in row)


def _replay_planted(
    data_dir: Path, tmp_path: Path, planted: str
) -> tuple[str, str, str, str]:
    fixture = write_mini_fixture(tmp_path, frames=1, planted=planted)
    conn = open_test_db(data_dir)
    try:
        result = replay_fixture(fixture, conn, instant=True, spool_dir=data_dir / "spool")
        assert result.extracted == 1
        assert result.deletion_verified == 1
        text = str(conn.execute("SELECT text_redacted FROM frame_text").fetchone()[0])
        jobs = _table_blob(conn, "jobs")
        audit = _table_blob(conn, "audit_events")
        tables = "\n".join(
            _table_blob(conn, name)
            for name in ("frames", "working_days", "frame_text", "settings")
        )
        return text, jobs, audit, tables
    finally:
        conn.close()


def test_planted_otp_phrasings_never_reach_frame_text(
    data_dir: Path, key_provider: object, tmp_path: Path
) -> None:
    secrets = ("739104", "482913", "4829", "7391")
    planted = (
        "Your code is 739104. Code: 482913. Your security code: 482913. "
        "482913 is your verification code. "
        "G-482913 is your Google verification code. "
        "PIN: 4829 Passcode: 7391 one-time code 482913"
    )
    text, jobs, audit, tables = _replay_planted(data_dir, tmp_path, planted)
    for secret in secrets:
        assert secret not in text
        assert secret not in jobs
        assert secret not in audit
        assert secret not in tables
    assert PLACEHOLDER_OTP in text
    assert "14 Nov" not in planted
    clean = "Demo 14 Nov section 3 amount 1200 zip code 90210"
    clean_dir = data_dir / "clean-otp"
    clean_dir.mkdir()
    clean_text, _, _, _ = _replay_planted(clean_dir, tmp_path / "clean-otp", clean)
    assert "1200" in clean_text
    assert "90210" in clean_text
    assert "14 Nov" in clean_text


def test_planted_spaced_iban_never_reaches_frame_text(
    data_dir: Path, key_provider: object, tmp_path: Path
) -> None:
    ibans = (
        "GB82 WEST 1234 5698 7654 32",
        "DE89 3704 0044 0532 0130 00",
        "GB82WEST12345698765432",
    )
    planted = "Pay " + " and ".join(ibans)
    text, jobs, audit, tables = _replay_planted(data_dir, tmp_path, planted)
    for iban in ibans:
        assert iban not in text
        assert iban.replace(" ", "") not in text.replace(" ", "")
        assert iban not in jobs
        assert iban not in audit
        assert iban not in tables
    assert PLACEHOLDER_IBAN in text


def test_planted_split_otp_never_reaches_frame_text(
    data_dir: Path, key_provider: object, tmp_path: Path
) -> None:
    secrets = ("482-913", "482 913", "7391-0428", "48 29 13", "4829 1374")
    planted = (
        "Your WhatsApp code: 482-913. Your code is 482 913. "
        "Passcode: 7391-0428 OTP 48 29 13 one-time code 4829 1374"
    )
    text, jobs, audit, tables = _replay_planted(data_dir, tmp_path, planted)
    for secret in secrets:
        assert secret not in text
        assert secret.replace(" ", "").replace("-", "") not in text.replace(" ", "").replace(
            "-", ""
        )
        assert secret not in jobs
        assert secret not in audit
        assert secret not in tables
    assert PLACEHOLDER_OTP in text


def test_planted_failed_checksum_iban_never_reaches_frame_text(
    data_dir: Path, key_provider: object, tmp_path: Path
) -> None:
    ibans = ("GB83 WEST 1234 5698 7654 32", "GB83WEST12345698765432")
    planted = "Pay " + " and ".join(ibans)
    text, jobs, audit, tables = _replay_planted(data_dir, tmp_path, planted)
    for iban in ibans:
        assert iban not in text
        assert iban.replace(" ", "") not in text.replace(" ", "")
        assert iban not in jobs
        assert iban not in audit
        assert iban not in tables
    assert PLACEHOLDER_SECRET in text
    assert PLACEHOLDER_IBAN not in text


def test_planted_code_phrases_are_not_over_redacted(
    data_dir: Path, key_provider: object, tmp_path: Path
) -> None:
    planted = (
        "PR 1234 code review. code freeze on 2026-11-03. "
        "Login page loads in 1200 ms. Pin the 2025 roadmap."
    )
    text, _, _, _ = _replay_planted(data_dir, tmp_path, planted)
    assert "1234" in text
    assert "2026-11-03" in text
    assert "1200" in text
    assert "2025" in text
    assert PLACEHOLDER_OTP not in text


def test_planted_uk_nino_never_reaches_frame_text(
    data_dir: Path, key_provider: object, tmp_path: Path
) -> None:
    ninos = ("QQ 12 34 56 C", "QQ123456C")
    planted = "National insurance " + " or ".join(ninos)
    text, jobs, audit, tables = _replay_planted(data_dir, tmp_path, planted)
    for nino in ninos:
        assert nino not in text
        assert nino.replace(" ", "") not in text.replace(" ", "")
        assert nino not in jobs
        assert nino not in audit
        assert nino not in tables
    assert PLACEHOLDER_SECRET in text


def test_dedup_audit_has_no_hashes_or_ocr(
    data_dir: Path, key_provider: object, tmp_path: Path
) -> None:
    fixture = write_mini_fixture(tmp_path, frames=2, planted="PLANTED_DEDUP_OCR_LINE")
    conn = open_test_db(data_dir)
    try:
        result = replay_fixture(fixture, conn, instant=True, spool_dir=data_dir / "spool")
        assert result.deduped == 1
        details = [
            str(row[0])
            for row in conn.execute(
                "SELECT detail_json FROM audit_events WHERE kind = ?", (KIND_DEDUPED,)
            ).fetchall()
        ]
        assert details
        joined = "\n".join(details)
        assert "PLANTED_DEDUP_OCR_LINE" not in joined
        assert "phash" not in joined
        assert "dhash" not in joined
        assert "ocr" not in joined
    finally:
        conn.close()
