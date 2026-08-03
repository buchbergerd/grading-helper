"""Alembic migration tests (docs/open-questions.md #11).

Two things these guard against, neither exercised by every other test's own ``init_db()``
(``create_all``) fixture:

1. ``alembic upgrade head`` actually runs cleanly against a brand-new database — this is
   production's only schema-creation path (``app/migrations.py``), never exercised otherwise
   since every other test builds its schema via ``create_all`` directly.
2. The migration history and the current ORM models don't drift apart: a model change with no
   matching migration would pass every other test in this suite (they never touch the migration
   history) while silently breaking every real deployment upgrading from an older revision.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine

from app import models  # noqa: F401  (registers every mapper on Base.metadata)
from app.config import get_settings
from app.db import Base
from app.migrations import run_migrations


@pytest.fixture
def migrated_db_url(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    """Run migrations against a fresh tmp_path database; return its URL.

    ``get_settings()`` is process-wide-cached (``lru_cache``), so the env var must be set and
    the cache cleared before *and* after — otherwise this leaks a stale database URL into
    whichever test happens to call ``get_settings()`` next in the same process.
    """
    url = f"sqlite:///{tmp_path / 'migrations_test.db'}"
    monkeypatch.setenv("GRADINGHELPER_DATABASE_URL", url)
    get_settings.cache_clear()
    try:
        run_migrations()
        yield url
    finally:
        get_settings.cache_clear()


def test_upgrade_head_runs_cleanly_against_an_empty_database(migrated_db_url: str) -> None:
    engine = create_engine(migrated_db_url)
    try:
        with engine.connect() as conn:
            tables = (
                conn.exec_driver_sql("SELECT name FROM sqlite_master WHERE type='table'")
                .scalars()
                .all()
            )
    finally:
        engine.dispose()
    # Every model's table, plus Alembic's own bookkeeping table.
    expected = {table.name for table in Base.metadata.sorted_tables} | {"alembic_version"}
    assert expected <= set(tables)


def test_migration_history_matches_current_models(migrated_db_url: str) -> None:
    """Autogenerate diff between the post-upgrade database and ``Base.metadata`` must be empty.

    A model changed without a paired migration would still pass every other test in this suite
    (they all build their schema via ``create_all``, never via the migration history) — this is
    the one test that would catch it.
    """
    engine = create_engine(migrated_db_url)
    try:
        with engine.connect() as conn:
            context = MigrationContext.configure(conn)
            diff = compare_metadata(context, Base.metadata)
    finally:
        engine.dispose()
    assert diff == [], f"model/migration drift detected: {diff!r}"


def test_run_migrations_is_idempotent(migrated_db_url: str) -> None:
    """Running migrations again against an already up-to-date database is a no-op, not an error."""
    run_migrations()
