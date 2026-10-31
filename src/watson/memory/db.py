"""Encrypted SQLite (SQLCipher) connection, WAL, FTS5, and sqlite-vec."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlcipher3 import dbapi2 as sqlcipher

from watson.memory.keys import KeyProvider


class DatabaseError(RuntimeError):
    """Failed to open or use the encrypted database."""


def iso_now(moment: datetime | None = None) -> str:
    value = (moment or datetime.now(UTC)).astimezone(UTC)
    return value.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def connect(
    path: Path,
    key: bytes,
    *,
    load_vec: bool = True,
) -> sqlcipher.Connection:
    """Open ``path`` with SQLCipher, apply the raw key, enable WAL.

    The key is applied via a bound parameter and is never logged.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlcipher.connect(str(path))
    try:
        _apply_key(conn, key)
        conn.execute("SELECT count(*) FROM sqlite_master").fetchone()
    except sqlcipher.DatabaseError as exc:
        conn.close()
        raise DatabaseError(f"unable to open encrypted database at {path}") from exc
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.row_factory = sqlcipher.Row
    if load_vec:
        load_sqlite_vec(conn)
    return conn


def _apply_key(conn: sqlcipher.Connection, key: bytes) -> None:
    # SQLCipher rejects bound parameters for PRAGMA key ("near '?'").
    # The statement is never logged; do not wrap this execute in tracing.
    conn.execute(f"""PRAGMA key = "x'{key.hex()}'" """)


def load_sqlite_vec(conn: sqlcipher.Connection) -> str:
    """Load sqlite-vec as an extension on this SQLCipher connection."""
    import sqlite_vec

    if not hasattr(conn, "enable_load_extension"):
        raise DatabaseError("SQLCipher build cannot load extensions (needed for sqlite-vec)")
    conn.enable_load_extension(True)
    try:
        sqlite_vec.load(conn)
    finally:
        conn.enable_load_extension(False)
    row = conn.execute("SELECT vec_version()").fetchone()
    if row is None:
        raise DatabaseError("sqlite-vec loaded but vec_version() returned nothing")
    return str(row[0])


def fts5_available(conn: sqlcipher.Connection) -> bool:
    options = [str(row[0]) for row in conn.execute("PRAGMA compile_options")]
    return any(opt.upper() == "ENABLE_FTS5" for opt in options)


def cipher_version(conn: sqlcipher.Connection) -> str:
    row = conn.execute("PRAGMA cipher_version").fetchone()
    return str(row[0]) if row else "unknown"


def journal_mode(conn: sqlcipher.Connection) -> str:
    row = conn.execute("PRAGMA journal_mode").fetchone()
    return str(row[0]).lower() if row else "unknown"


def assert_unreadable_without_key(path: Path) -> None:
    """stdlib sqlite3 must not be able to read an encrypted file."""
    plain = sqlite3.connect(str(path))
    try:
        plain.execute("SELECT name FROM sqlite_master").fetchall()
    except sqlite3.DatabaseError:
        return
    finally:
        plain.close()
    raise DatabaseError(f"{path} is readable without a SQLCipher key")


@contextmanager
def open_database(
    path: Path,
    provider: KeyProvider,
    *,
    load_vec: bool = True,
) -> Iterator[sqlcipher.Connection]:
    conn = connect(path, provider.get_key(), load_vec=load_vec)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def table_names(conn: sqlcipher.Connection) -> list[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
    ).fetchall()
    return [str(row[0]) for row in rows]


def row_to_dict(row: Any) -> dict[str, Any]:
    return {key: row[key] for key in row.keys()}
