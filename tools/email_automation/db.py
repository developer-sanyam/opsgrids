"""Database connection and session factory for email automation.

Supports SQLite (built-in sqlite3) and PostgreSQL (psycopg2/asyncpg).
"""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from tools.email_automation.config import EmailConfig
from tools.email_automation.models import Base

_engine: Engine | None = None
_sessionmaker: sessionmaker[Session] | None = None


def get_engine(db_url: str | None = None) -> Engine:
    global _engine
    if _engine is None:
        if db_url is None:
            db_url = EmailConfig.from_env().db_url

        # Clean async prefixes if present for standard engine
        if db_url.startswith("sqlite+aiosqlite:///"):
            db_url = db_url.replace("sqlite+aiosqlite:///", "sqlite:///")
        elif db_url.startswith("postgresql+asyncpg://"):
            db_url = db_url.replace("postgresql+asyncpg://", "postgresql://")

        connect_args = {}
        if "sqlite" in db_url:
            connect_args["check_same_thread"] = False

        _engine = create_engine(
            db_url,
            echo=False,
            future=True,
            connect_args=connect_args,
        )
    return _engine


def get_sessionmaker(engine: Engine | None = None) -> sessionmaker[Session]:
    global _sessionmaker
    if _sessionmaker is None:
        eng = engine or get_engine()
        _sessionmaker = sessionmaker(
            bind=eng,
            class_=Session,
            expire_on_commit=False,
            autoflush=False,
        )
    return _sessionmaker


def init_db(engine: Engine | None = None) -> None:
    """Creates database tables if they do not exist."""
    eng = engine or get_engine()
    Base.metadata.create_all(eng)


@contextmanager
def get_session(engine: Engine | None = None) -> Generator[Session, None, None]:
    """Provides a transactional database session context."""
    sm = get_sessionmaker(engine)
    session = sm()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def close_db() -> None:
    """Disposes engine connection pool."""
    global _engine, _sessionmaker
    if _engine is not None:
        _engine.dispose()
        _engine = None
        _sessionmaker = None
