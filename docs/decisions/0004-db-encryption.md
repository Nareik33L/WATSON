# 0004 — Database encryption

**Status:** Accepted  
**Date:** 2026-10-31  
**Decides:** Engineering plan §14 #4 (SQLCipher vs application-level column encryption)  
**Related:** D7, §2.1, §6, M1

## Decision

Use **SQLCipher** (full-file encryption) via the maintained [`sqlcipher3`](https://pypi.org/project/sqlcipher3/) wheel, not Plan B column encryption.

The packaged binding is `sqlcipher3` **0.6.4** (require `>=0.6.2`, the first release that ships self-contained wheels). The SQLCipher amalgamation is **4.19.0 community**, built on SQLite **3.53.4**, with `ENABLE_FTS5` and `ENABLE_LOAD_EXTENSION`.

`sqlite-vec` **0.1.9** loads as a runtime extension on that SQLCipher connection. FTS5 virtual tables work in the same encrypted file. WAL is enabled after the key is applied.

The 256-bit raw key is held by a `KeyProvider`. Production uses macOS Keychain through `keyring` (`KeychainKeyProvider`). Tests use `InMemoryKeyProvider`. The key is never written to the data directory and never appears in `repr()` or structured logs. SQLCipher does not accept bound parameters for `PRAGMA key`; the hex key is interpolated only in that one in-process statement and that statement is never logged.

## Evidence

### Wheels for the target platforms (Python 3.12)

Published on PyPI for `sqlcipher3==0.6.4`:

| Platform | Wheel |
|---|---|
| Linux x86_64 | `sqlcipher3-0.6.4-cp312-cp312-manylinux_2_28_x86_64.whl` |
| macOS arm64 | `sqlcipher3-0.6.4-cp312-cp312-macosx_11_0_arm64.whl` |

`sqlite-vec==0.1.9` ships matching wheels:

| Platform | Wheel |
|---|---|
| Linux x86_64 | `sqlite_vec-0.1.9-py3-none-manylinux_2_17_x86_64.manylinux2014_x86_64.manylinux1_x86_64.whl` |
| macOS arm64 | `sqlite_vec-0.1.9-py3-none-macosx_11_0_arm64.whl` |

Both install without a compiler.

### Linux x86_64 proof (this repository, 2026-10-31)

On the development/CI Linux image, `uv add` installed the manylinux wheels above. A throwaway script then:

1. Opened a file with `sqlcipher3.dbapi2`, applied `PRAGMA key = "x'<64 hex chars>'"`.
2. Confirmed compile options include `ENABLE_FTS5` and `ENABLE_LOAD_EXTENSION`.
3. `PRAGMA journal_mode=WAL` returned `wal`.
4. `PRAGMA cipher_version` returned `4.19.0 community`.
5. Created `USING fts5(body)`, inserted a row, and `MATCH 'watson'` returned it.
6. `conn.enable_load_extension(True)` + `sqlite_vec.load(conn)` succeeded; `vec_version()` returned `v0.1.9`; a `vec0` virtual table accepted a 4-d vector.
7. Opening the same file with stdlib `sqlite3` (no key) raised `DatabaseError: file is not a database`.
8. Opening with SQLCipher and the wrong key raised the same class of error (SQLCipher logs `hmac check failed` to stderr; the key is not in that message).
9. The raw key bytes and the hex encoding were **not** present in the database file. The file header was ciphertext, not `SQLite format 3`.

The same checks are locked in `tests/unit/test_db_smoke.py` (run on Ubuntu CI) and in the path-filtered macos-15 job `.github/workflows/db-smoke.yml` (macos-14 images are in brownout).

### Key handling tests

`tests/unit/test_keys.py` asserts:

- `InMemoryKeyProvider` `repr()` and DEBUG logs do not contain the hex key.
- After `connect()`, no file under the data dir contains the raw or hex key.
- `KeychainKeyProvider` reads/writes only through `keyring` (mocked).

## Plan B (not taken)

If the wheels had failed to install on Linux x86_64 or macOS arm64, or if FTS5 / `sqlite-vec` could not run on the SQLCipher connection, the fallback was AES-GCM column encryption with `cryptography` and a tokenised-but-hashed FTS index. That path is unnecessary: the wheels satisfy D7 on both required platforms.

## Consequences

- One encrypted file (`watson.db` + WAL/SHM) holds relational rows, FTS5, and vectors.
- Alembic runs against a SQLCipher connection (`creator=` + `NullPool`); the first revision is `0001_m1_foundation`.
- A file copied off a stolen disk (FileVault off) is unreadable without the Keychain key. A local attacker running as the user can still use that key — documented in §6.
- On macOS the key lives in Keychain only; `WATSON_DB_KEY` is ignored. Linux developers/CI set `WATSON_DB_KEY` (64 hex characters) or install an `InMemoryKeyProvider` in-process. There is no file-backed keyring fallback, because that would write the key to disk.
