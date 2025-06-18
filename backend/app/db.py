"""Database engine, session management and shared column types."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import DateTime, TypeDecorator, create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import settings


class UTCDateTime(TypeDecorator):
    """Timezone-aware UTC timestamp that works on SQLite and PostgreSQL.

    SQLite has no native timezone support; the decorator guarantees that values
    coming out of the database are always timezone-aware UTC datetimes so the
    rest of the code never has to reason about naive timestamps.
    """

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Any) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    def process_result_value(self, value: datetime | None, dialect: Any) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)


class Base(DeclarativeBase):
    """Declarative base for every ORM model."""


def _engine_kwargs() -> dict[str, Any]:
    kwargs: dict[str, Any] = {"echo": settings.db_echo, "future": True}
    if settings.is_sqlite:
        kwargs["connect_args"] = {"check_same_thread": False, "timeout": 30}
    else:
        kwargs["pool_size"] = settings.db_pool_size
        kwargs["max_overflow"] = settings.db_max_overflow
        kwargs["pool_pre_ping"] = True
    return kwargs


engine: Engine = create_engine(settings.database_url, **_engine_kwargs())

if settings.is_sqlite:

    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_connection, _record):  # pragma: no cover - driver level
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


def get_db() -> Iterator[Session]:
    """FastAPI dependency yielding a transactional session."""
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def utcnow() -> datetime:
    return datetime.now(UTC)
