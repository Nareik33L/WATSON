"""Alembic environment. Connections come from watson.memory.db (SQLCipher)."""

from __future__ import annotations

from alembic import context
from sqlalchemy import create_engine
from sqlalchemy.pool import NullPool

from watson.config import load_config
from watson.memory.db import connect
from watson.memory.keys import get_key_provider

config = context.config


def run_migrations_offline() -> None:
    raise RuntimeError("WATSON migrations run online against the encrypted database")


def _creator():
    factory = config.attributes.get("db_connect")
    if factory is not None:
        return factory()
    settings = load_config()
    key = get_key_provider().get_key()
    return connect(settings.db_path, key, load_vec=False)


def run_migrations_online() -> None:
    engine = create_engine("sqlite://", creator=_creator, poolclass=NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection, render_as_batch=True)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
