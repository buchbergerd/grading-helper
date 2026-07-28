"""Database engine, session handling and schema creation.

Two SQLite pragmas are set on **every** connection (see :func:`_set_sqlite_pragmas`):

``PRAGMA foreign_keys=ON``
    SQLite ships with foreign-key enforcement *disabled*. Without this pragma the
    ``ondelete="CASCADE"`` clauses on our foreign keys are inert, which would silently break
    SPECIFICATION.md §13's requirement that deleting an exam cascades to all of its student
    registrations and points (leaving orphaned personal data behind — a data-protection issue,
    not just a tidiness one).

``PRAGMA journal_mode=WAL``
    Better read/write concurrency for the single-file deployment (§12/§13). Skipped for
    in-memory databases, where it is meaningless.

No Alembic in this milestone: :func:`init_db` just runs ``create_all``. This is deliberate —
the models are still churning; migrations get introduced once the schema settles.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings

_MEMORY_DATABASES = frozenset({":memory:", ""})


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""


def _is_memory_connection(cursor: sqlite3.Cursor) -> bool:
    """True if the connection's ``main`` database is in-memory (no backing file)."""
    for _seq, name, file in cursor.execute("PRAGMA database_list").fetchall():
        if name == "main":
            return not file
    return False


@event.listens_for(Engine, "connect")
def _set_sqlite_pragmas(dbapi_connection: Any, connection_record: Any) -> None:
    """Enable FK enforcement (and WAL for file-backed DBs) on every new SQLite connection."""
    # This listener is registered on the Engine class, so it fires for every engine in the
    # process — including any non-SQLite one a future milestone might add.
    if not isinstance(dbapi_connection, sqlite3.Connection):
        return
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA foreign_keys=ON")
        if not _is_memory_connection(cursor):
            cursor.execute("PRAGMA journal_mode=WAL")
    finally:
        cursor.close()


def _ensure_sqlite_directory(url: str) -> None:
    """Create the parent directory of a file-backed SQLite database if it does not exist."""
    parsed = make_url(url)
    if not parsed.drivername.startswith("sqlite"):
        return
    database = parsed.database
    if database is None or database in _MEMORY_DATABASES:
        return
    parent = Path(database).expanduser().parent
    if str(parent):
        parent.mkdir(parents=True, exist_ok=True)


def create_engine_for(url: str, **kwargs: Any) -> Engine:
    """Create an :class:`~sqlalchemy.Engine` for ``url``, with SQLite-appropriate settings."""
    parsed = make_url(url)
    connect_args: dict[str, Any] = dict(kwargs.pop("connect_args", {}))
    if parsed.drivername.startswith("sqlite"):
        _ensure_sqlite_directory(url)
        # FastAPI runs request handlers in a threadpool; a connection may be used from a
        # different thread than the one that created it. Session-per-request keeps this safe.
        connect_args.setdefault("check_same_thread", False)
    return create_engine(url, connect_args=connect_args, **kwargs)


# Session factory. Left unbound at import time and bound lazily by get_engine() so that merely
# importing this module never opens a database or creates ./data/ — tests must be able to point
# at their own database.
SessionLocal = sessionmaker(class_=Session, autoflush=False, expire_on_commit=False)

_engine: Engine | None = None


def get_engine() -> Engine:
    """Return the lazily-created default engine (from ``Settings.database_url``)."""
    global _engine
    if _engine is None:
        _engine = create_engine_for(get_settings().database_url)
        SessionLocal.configure(bind=_engine)
    return _engine


def dispose_engine() -> None:
    """Dispose of and forget the default engine (used by tests and shutdown paths)."""
    global _engine
    if _engine is not None:
        _engine.dispose()
        _engine = None


def init_db(engine: Engine | None = None) -> None:
    """Create all tables that do not exist yet.

    Imports :mod:`app.models` for its side effect of registering every mapper with
    :data:`Base.metadata` (done inside the function to avoid an import cycle).
    """
    from app import models  # noqa: F401  (registers the mappers)

    Base.metadata.create_all(engine if engine is not None else get_engine())


def get_db() -> Iterator[Session]:
    """FastAPI dependency: one :class:`Session` per request, always closed."""
    get_engine()  # ensure SessionLocal is bound
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
