"""WATSON command-line interface: run, replay, models, db, doctor."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from sqlcipher3 import dbapi2 as sqlcipher

from watson import __version__
from watson.config import ConfigError, ensure_runtime_dirs, is_cloud_synced_path, load_config
from watson.config.schema import WatsonConfig
from watson.memory.db import (
    DatabaseError,
    cipher_version,
    connect,
    fts5_available,
    journal_mode,
    load_sqlite_vec,
)
from watson.memory.keys import KeyProviderError, get_key_provider
from watson.memory.migrate import revision_status, upgrade_head
from watson.privacy.logging import configure_logging
from watson.privacy.network import runtime_network_guard

_STUB_COMMANDS = ("run", "replay", "models")


def main(argv: Sequence[str] | None = None) -> int:
    configure_logging()
    with runtime_network_guard():
        return _main(argv)


def _main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="watson",
        description="Local-first work memory.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"watson {__version__}",
    )
    subparsers = parser.add_subparsers(dest="command")

    doctor = subparsers.add_parser(
        "doctor",
        help="Print resolved config paths and database status (no secrets)",
    )
    doctor.set_defaults(handler=_cmd_doctor)

    db = subparsers.add_parser("db", help="Encrypted-database migrations")
    db_sub = db.add_subparsers(dest="db_command")
    upgrade = db_sub.add_parser("upgrade", help="Apply Alembic migrations to head")
    upgrade.set_defaults(handler=_cmd_db_upgrade)
    status = db_sub.add_parser("status", help="Show current and head Alembic revision")
    status.set_defaults(handler=_cmd_db_status)

    for name in _STUB_COMMANDS:
        subparsers.add_parser(name, help="not implemented")

    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help(sys.stderr)
        return 1
    if args.command in _STUB_COMMANDS:
        print("not implemented", file=sys.stderr)
        return 1
    if args.command == "db" and getattr(args, "db_command", None) is None:
        db.print_help(sys.stderr)
        return 1

    handler = getattr(args, "handler", None)
    if handler is None:
        parser.print_help(sys.stderr)
        return 1
    try:
        return int(handler(args))
    except ConfigError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except (KeyProviderError, DatabaseError) as exc:
        print(str(exc), file=sys.stderr)
        return 2


def _cmd_doctor(_args: argparse.Namespace) -> int:
    config = load_config()
    lines = [
        "WATSON doctor",
        f"config_path: {config.config_path}{_absent(config.config_path)}",
        f"data_dir: {config.data_dir}",
        f"spool_dir: {config.spool_dir}{_absent(config.spool_dir)}",
        f"db_path: {config.db_path}{_absent(config.db_path)}",
        f"logs_dir: {config.logs_dir}{_absent(config.logs_dir)}",
        f"models_dir: {config.models_dir}",
        f"session_dir: {config.session_dir}",
    ]
    if is_cloud_synced_path(config.data_dir):
        lines.append("warning: data_dir looks like a cloud-synced volume")
    lines.extend(_db_status_lines(config))
    print("\n".join(lines))
    return 0


def _cmd_db_upgrade(_args: argparse.Namespace) -> int:
    config = load_config()
    ensure_runtime_dirs(config)
    key = get_key_provider().get_key()
    current = upgrade_head(config.db_path, key)
    if config.db_path.is_file():
        config.db_path.chmod(0o600)
    print(f"alembic_current: {current or '-'}")
    print("db_status: ok")
    return 0


def _cmd_db_status(_args: argparse.Namespace) -> int:
    config = load_config()
    print("\n".join(_db_status_lines(config)))
    return 0


def _db_status_lines(config: WatsonConfig) -> list[str]:
    lines: list[str] = []
    provider_label, key = _try_key()
    lines.append(f"key_provider: {provider_label}")
    if not config.db_path.is_file():
        lines.append("db_status: missing")
        if key is not None:
            _current, heads = revision_status(config.db_path, key)
            lines.append("alembic_current: -")
            lines.append(f"alembic_head: {_format_heads(heads)}")
        return lines

    if key is None:
        lines.append("db_status: present")
        return lines

    try:
        conn = connect(config.db_path, key)
    except DatabaseError:
        lines.append("db_status: unreadable")
        return lines

    try:
        current, heads = revision_status(config.db_path, key)
        vec = _vec_version(conn)
        lines.append("db_status: ok")
        lines.append(f"db_cipher: {cipher_version(conn)}")
        lines.append(f"db_journal_mode: {journal_mode(conn)}")
        lines.append(f"db_fts5: {'available' if fts5_available(conn) else 'missing'}")
        lines.append(f"db_sqlite_vec: {vec}")
        lines.append(f"alembic_current: {current or '-'}")
        lines.append(f"alembic_head: {_format_heads(heads)}")
        if current is not None and current in heads:
            lines.append("alembic_status: head")
        elif current is None:
            lines.append("alembic_status: empty")
        else:
            lines.append("alembic_status: behind")
    finally:
        conn.close()
    return lines


def _try_key() -> tuple[str, bytes | None]:
    try:
        provider = get_key_provider()
    except KeyProviderError:
        return "unavailable", None
    label = type(provider).__name__
    if label == "InMemoryKeyProvider":
        return "in-memory", provider.get_key()
    if label == "KeychainKeyProvider":
        return "keychain", provider.get_key()
    return label, provider.get_key()


def _vec_version(conn: sqlcipher.Connection) -> str:
    try:
        row = conn.execute("SELECT vec_version()").fetchone()
    except sqlcipher.DatabaseError:
        return load_sqlite_vec(conn)
    return str(row[0]) if row else load_sqlite_vec(conn)


def _format_heads(heads: tuple[str, ...]) -> str:
    return ",".join(heads) if heads else "-"


def _absent(path: Path) -> str:
    return "" if path.exists() else " (absent)"


if __name__ == "__main__":
    raise SystemExit(main())
