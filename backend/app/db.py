"""Database engine / session management."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from .config import settings
from .models import Base

# Registers drill mode's tables on Base.metadata for the side effect, so init_db()
# creates them and autogenerate can see them.
from .drill import models as _drill_models  # noqa: E402,F401

_url = settings.resolved_database_url()
_is_sqlite = _url.startswith("sqlite")

engine = create_engine(
    _url,
    echo=False,
    future=True,
    connect_args={"check_same_thread": False} if _is_sqlite else {},
)

if _is_sqlite:

    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_conn, _record):  # pragma: no cover - driver hook
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")  # concurrent CLI writes + API reads
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)


def init_db() -> None:
    Base.metadata.create_all(engine)


@contextmanager
def session_scope() -> Iterator[Session]:
    """Transactional scope for scripts and pipeline code."""
    s = SessionLocal()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


def get_db() -> Iterator[Session]:
    """FastAPI dependency."""
    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()
