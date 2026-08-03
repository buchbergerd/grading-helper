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
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, insert, select

from app import models  # noqa: F401  (registers every mapper on Base.metadata)
from app.config import get_settings
from app.db import Base, create_engine_for, init_db
from app.migrations import _ALEMBIC_INI, run_migrations
from app.models import User


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


def test_run_migrations_adopts_a_pre_alembic_database_via_stamp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A database built by ``create_all`` before Alembic existed must be *stamped*, not replayed.

    Every developer's pre-existing local ``backend/data/gradinghelper.db`` is exactly this case:
    full schema, no ``alembic_version`` row. Replaying the initial migration's ``CREATE TABLE``
    statements against it fails with "table already exists" — this is what actually happened the
    first time this shipped, killing a running dev server's reload worker. This test builds that
    exact starting state (schema via ``init_db``/``create_all``, pre-existing data, no Alembic
    involvement at all) and asserts ``run_migrations()`` both succeeds and leaves the data alone.
    """
    url = f"sqlite:///{tmp_path / 'legacy.db'}"
    engine = create_engine_for(url)
    try:
        init_db(engine)
        with engine.connect() as connection:
            tables = (
                connection.exec_driver_sql("SELECT name FROM sqlite_master WHERE type='table'")
                .scalars()
                .all()
            )
            assert "alembic_version" not in tables
        with engine.begin() as connection:
            connection.execute(
                insert(User).values(username="dozentin", password_hash="not-a-real-hash")
            )
    finally:
        engine.dispose()

    monkeypatch.setenv("GRADINGHELPER_DATABASE_URL", url)
    get_settings.cache_clear()
    try:
        run_migrations()
    finally:
        get_settings.cache_clear()

    head = ScriptDirectory.from_config(Config(str(_ALEMBIC_INI))).get_current_head()
    check_engine = create_engine(url)
    try:
        with check_engine.connect() as connection:
            stamped = MigrationContext.configure(connection).get_current_revision()
            with check_engine.begin() as bound:
                usernames = bound.execute(select(User.username)).scalars().all()
    finally:
        check_engine.dispose()

    assert stamped == head
    assert usernames == ["dozentin"]
