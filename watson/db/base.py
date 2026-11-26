"""SQLAlchemy base. The metadata is shared by the migration runner."""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass
