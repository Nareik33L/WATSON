"""SQLCipher + FTS5 + sqlite-vec smoke tests (Linux and macos-15 CI)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from watson.memory.db import (
    DatabaseError,
    assert_unreadable_without_key,
    cipher_version,
    connect,
    fts5_available,
    journal_mode,
)
from watson.memory.migrate import upgrade_head

TEST_KEY = bytes.fromhex("11" * 32)


def test_sqlcipher_fts5_vec_and_unreadability(tmp_path: Path) -> None:
    path = tmp_path / "watson.db"
    conn = connect(path, TEST_KEY)
    try:
        assert fts5_available(conn)
        assert journal_mode(conn) == "wal"
        assert "sqlcipher" in cipher_version(conn).lower() or cipher_version(conn)

        conn.execute("CREATE VIRTUAL TABLE fts_smoke USING fts5(body)")
        conn.execute("INSERT INTO fts_smoke(body) VALUES ('hello watson encrypted')")
        hit = conn.execute("SELECT body FROM fts_smoke WHERE fts_smoke MATCH 'watson'").fetchone()
        assert hit is not None
        assert hit[0] == "hello watson encrypted"

        version = conn.execute("SELECT vec_version()").fetchone()
        assert version is not None
        assert str(version[0]).startswith("v")
        conn.execute("CREATE VIRTUAL TABLE vec_smoke USING vec0(sample_embedding float[4])")
        conn.execute(
            "INSERT INTO vec_smoke(rowid, sample_embedding) VALUES (1, '[0.1, 0.2, 0.3, 0.4]')"
        )
        assert conn.execute("SELECT rowid FROM vec_smoke").fetchone()[0] == 1
        conn.commit()
    finally:
        conn.close()

    assert_unreadable_without_key(path)

    raw = sqlite3.connect(str(path))
    with pytest.raises(sqlite3.DatabaseError, match="not a database"):
        raw.execute("SELECT name FROM sqlite_master").fetchall()
    raw.close()

    from sqlcipher3 import dbapi2 as sqlcipher

    locked = sqlcipher.connect(str(path))
    locked.execute(f"""PRAGMA key = "x'{"00" * 32}'" """)
    with pytest.raises(sqlcipher.DatabaseError):
        locked.execute("SELECT name FROM sqlite_master").fetchall()
    locked.close()

    blob = path.read_bytes()
    assert TEST_KEY not in blob
    assert TEST_KEY.hex().encode() not in blob
    assert not blob.startswith(b"SQLite format 3")


@pytest.mark.parametrize(
    "bad_key",
    [b"", b"short", b"\x00" * 31, b"\x00" * 33, b"\x00" * 64],
    ids=["empty", "short", "31", "33", "64"],
)
def test_connect_rejects_non_32_byte_keys(tmp_path: Path, bad_key: bytes) -> None:
    path = tmp_path / "watson.db"
    with pytest.raises(DatabaseError, match="32 bytes"):
        connect(path, bad_key, load_vec=False)
    assert not path.exists()


def test_wrong_key_raises_database_error(tmp_path: Path) -> None:
    path = tmp_path / "watson.db"
    conn = connect(path, TEST_KEY, load_vec=False)
    conn.close()
    with pytest.raises(DatabaseError, match="unable to open"):
        connect(path, bytes.fromhex("22" * 32), load_vec=False)


def test_migrations_create_m1_tables(tmp_path: Path) -> None:
    path = tmp_path / "watson.db"
    assert upgrade_head(path, TEST_KEY) == "0002_frame_text"
    conn = connect(path, TEST_KEY, load_vec=False)
    try:
        names = {
            row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
    finally:
        conn.close()
    assert {"frames", "jobs", "audit_events", "working_days", "settings", "frame_text"} <= names
    assert "claims" not in names
