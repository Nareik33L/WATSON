"""WATSON command-line interface: run, replay, models, db, doctor, status, dashboard."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from sqlcipher3 import dbapi2 as sqlcipher

from watson import __version__
from watson.config import ConfigError, ensure_runtime_dirs, is_cloud_synced_path, load_config
from watson.config.schema import WatsonConfig
from watson.dashboard.bind import DEFAULT_PORT, BindError
from watson.dashboard.serve import run_dashboard
from watson.dashboard.tokens import TokenError
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
from watson.pipeline.replay import ReplayError, replay_fixture
from watson.pipeline.status import StatusReport, collect_status
from watson.privacy.logging import configure_logging
from watson.privacy.network import runtime_network_guard

_STUB_COMMANDS = ("run", "models")


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

    status = subparsers.add_parser(
        "status",
        help="Print frame/job counts and last replay (for the status page)",
    )
    status.set_defaults(handler=_cmd_status)

    dashboard = subparsers.add_parser(
        "dashboard",
        help="Serve the local status page on 127.0.0.1 (loopback only)",
    )
    dashboard.add_argument(
        "--host",
        default="127.0.0.1",
        help="Bind address (must be 127.0.0.1)",
    )
    dashboard.add_argument(
        "--port",
        type=int,
        default=DEFAULT_PORT,
        help=f"Preferred port (default {DEFAULT_PORT}; falls back if busy)",
    )
    dashboard.add_argument(
        "--open",
        action="store_true",
        help="Open the one-time auth URL in the local browser",
    )
    dashboard.set_defaults(handler=_cmd_dashboard)

    replay = subparsers.add_parser(
        "replay",
        help="Validate a fixture and ingest it through the spool (D4 delete-after-extract)",
    )
    replay.add_argument("fixture_dir", type=Path, help="Fixture directory with manifest.json")
    pace = replay.add_mutually_exclusive_group()
    pace.add_argument(
        "--speed",
        type=float,
        metavar="N",
        help="Replay N× captured_at realtime (default 1 if --instant is not set)",
    )
    pace.add_argument(
        "--instant",
        action="store_true",
        help="Ingest every frame with no inter-frame delay",
    )
    replay.set_defaults(handler=_cmd_replay)

    db = subparsers.add_parser("db", help="Encrypted-database migrations")
    db_sub = db.add_subparsers(dest="db_command")
    upgrade = db_sub.add_parser("upgrade", help="Apply Alembic migrations to head")
    upgrade.set_defaults(handler=_cmd_db_upgrade)
    db_status = db_sub.add_parser("status", help="Show current and head Alembic revision")
    db_status.set_defaults(handler=_cmd_db_status)

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
    except (KeyProviderError, DatabaseError, TokenError, BindError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except ReplayError as exc:
        print(str(exc), file=sys.stderr)
        return 1


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


def _cmd_status(_args: argparse.Namespace) -> int:
    config = load_config()
    overrides = config.exclusions.overridden_default_bundle_ids()
    if not config.db_path.is_file():
        print("db_status: missing")
        print("\n".join(StatusReport({}, {}, None, exclusion_overrides=overrides).lines()))
        return 0
    conn = _open_db(config, upgrade=True)
    try:
        print("\n".join(collect_status(conn, exclusions=config.exclusions).lines()))
    finally:
        conn.close()
    return 0


def _cmd_dashboard(args: argparse.Namespace) -> int:
    return run_dashboard(host=str(args.host), port=int(args.port), open_browser=bool(args.open))


def _cmd_replay(args: argparse.Namespace) -> int:
    config = load_config()
    instant = bool(args.instant)
    speed = 1.0 if args.speed is None else float(args.speed)
    conn = _open_db(config, upgrade=True)
    try:
        result = replay_fixture(
            args.fixture_dir,
            conn,
            instant=instant,
            speed=speed,
        )
    finally:
        conn.close()
    print(f"replay: {result.fixture_name}")
    print(f"frames: {result.frame_count}")
    print(f"ingested: {result.ingested}")
    print(f"extracted: {result.extracted}")
    print(f"deleted: {result.deleted}")
    print(f"deletion_verified: {result.deletion_verified}")
    print(f"deduped: {result.deduped}")
    print(f"segments: {result.segments}")
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


def _open_db(config: WatsonConfig, *, upgrade: bool = False) -> sqlcipher.Connection:
    ensure_runtime_dirs(config)
    key = get_key_provider().get_key()
    if upgrade:
        upgrade_head(config.db_path, key)
    if config.db_path.is_file():
        config.db_path.chmod(0o600)
    return connect(config.db_path, key)


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
    if label == "EnvKeyProvider":
        return "env", provider.get_key()
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
