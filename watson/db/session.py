"""Engine and session factory.

SQLite is the Phase 1 store. The session factory is the seam a PostgreSQL
deployment would replace: repositories talk to a Session, not to sqlite3.
"""

import logging
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker

log = logging.getLogger("watson.db")


def _sqlite_on_connect(dbapi_connection, _connection_record) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.close()


def make_engine(database_path: Path) -> Engine:
    database_path.parent.mkdir(parents=True, exist_ok=True)
    url = "sqlite:///" + database_path.resolve().as_posix()
    engine = create_engine(url, future=True)
    event.listen(engine, "connect", _sqlite_on_connect)
    log.info("Database %s", database_path)
    return engine


def make_session_factory(engine: Engine) -> sessionmaker:
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)
