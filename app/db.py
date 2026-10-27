"""SQLite engine factory and declarative base.

SQLite is chosen deliberately: the deploy server (LoscoServer) has 1 core and
1.9 GB RAM with Chatwoot already running on it, so a second database server is
not viable for this workload. WAL mode allows reads while a write happens.
"""

from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Declarative base for all models."""


def make_engine(database_path: Path):
    """Create a SQLite engine for `database_path` with WAL enabled."""
    database_path = Path(database_path)
    database_path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(
        f"sqlite:///{database_path}",
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_connection, _record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    return engine
