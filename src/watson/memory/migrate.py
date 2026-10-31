"""Alembic helpers for the encrypted database."""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine
from sqlalchemy.pool import NullPool

from watson.memory.db import connect

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"


def alembic_config(path: Path, key: bytes, *, load_vec: bool = False) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
    cfg.set_main_option("sqlalchemy.url", "sqlite://")
    cfg.attributes["db_connect"] = lambda: connect(path, key, load_vec=load_vec)
    return cfg


def upgrade_head(path: Path, key: bytes) -> str | None:
    command.upgrade(alembic_config(path, key), "head")
    current, _heads = revision_status(path, key)
    return current


def revision_status(path: Path, key: bytes) -> tuple[str | None, tuple[str, ...]]:
    script = ScriptDirectory(str(MIGRATIONS_DIR))
    heads = tuple(script.get_heads())
    if not path.exists():
        return None, heads
    engine = create_engine(
        "sqlite://",
        creator=lambda: connect(path, key, load_vec=False),
        poolclass=NullPool,
    )
    with engine.connect() as connection:
        context = MigrationContext.configure(connection)
        current = context.get_current_revision()
    return current, heads
