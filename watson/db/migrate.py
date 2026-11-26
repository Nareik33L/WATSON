"""Migration runner.

001 creates the current schema. Later phases add numbered steps recorded in
schema_migrations. The runner is intentionally small: one forwards path, no
silent drift.
"""

import logging
from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.engine import Engine

from watson.db.base import Base

log = logging.getLogger("watson.db")


def migrate(engine: Engine) -> None:
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version TEXT PRIMARY KEY,
                    applied_at TEXT NOT NULL
                )
                """
            )
        )
        applied = {row[0] for row in connection.execute(text("SELECT version FROM schema_migrations"))}
        if "001_initial" in applied:
            log.info("Migrations current at 001_initial")
            return
        log.info("Applying migration 001_initial")
        Base.metadata.create_all(connection)
        connection.execute(
            text("INSERT INTO schema_migrations (version, applied_at) VALUES (:version, :applied_at)"),
            {"version": "001_initial", "applied_at": datetime.now(timezone.utc).isoformat()},
        )
        log.info("Migration 001_initial applied")
