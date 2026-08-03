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
from sqlalchemy import create_engine, insert, select, text

from alembic import command
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


def test_bonus_points_migration_preserves_registrations_and_carries_the_max_forward(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression pin for a real bug found while writing migration ``8ab4f293a4ce``.

    Importing ``app.db`` registers its ``PRAGMA foreign_keys=ON`` listener on every SQLAlchemy
    ``Engine`` in the process (``app/db.py::_set_sqlite_pragmas`` is attached to the *class*, not
    one instance) — including Alembic's own migration-only engine. SQLite refuses to change that
    pragma once a transaction is open, which silently defeats Alembic's own "turn foreign_keys
    off while batch mode recreates a table" safety net: by the time a batch op tries, one already
    is. Recreating any table with incoming ``ondelete="CASCADE"`` foreign keys — here, "exams",
    which "student_registrations"/"exercises"/"grade_thresholds" all reference — then runs its
    internal ``DROP TABLE`` step with enforcement genuinely on, which cascade-deletes every child
    row for real. Fixed in ``alembic/env.py::run_migrations_online`` (``PRAGMA foreign_keys=OFF``
    before ``context.begin_transaction()`` opens anything); this test seeds exactly that shape at
    the revision immediately before ``8ab4f293a4ce`` and asserts the registrations survive the
    upgrade. It also pins that migration's data step: the exam's new ``bonus_points`` carries
    forward the *highest* previously entered non-excluded student value, never the excluded
    student's.
    """
    url = f"sqlite:///{tmp_path / 'bonus_migration.db'}"
    monkeypatch.setenv("GRADINGHELPER_DATABASE_URL", url)
    get_settings.cache_clear()
    config = Config(str(_ALEMBIC_INI))
    try:
        # The revision immediately before 8ab4f293a4ce — bonus_points still lives on
        # student_registrations, exactly the pre-migration shape.
        command.upgrade(config, "fb8602eb97b1")

        engine = create_engine(url)
        try:
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO users (id, username, password_hash, is_admin, is_active, "
                        "created_at) VALUES (1,'a','x',0,1,'2024-01-01')"
                    )
                )
                connection.execute(
                    text(
                        "INSERT INTO lectures (id, name, owner_id, created_at) "
                        "VALUES (1,'L',1,'2024-01-01')"
                    )
                )
                connection.execute(
                    text(
                        "INSERT INTO exams (id, lecture_id, owner_id, semester, termin, "
                        "exam_date, bonus_mode, created_at) VALUES "
                        "(1,1,1,'S','T',NULL,'ALWAYS','2024-01-01')"
                    )
                )
                # One excluded student with a much larger bonus_points, which must be ignored —
                # excluded students never contributed a grade to begin with (§5.3).
                for reg_id, matrikelnummer, excluded, bonus in (
                    (1, "m1", 0, "2.5"),
                    (2, "m2", 0, "1.0"),
                    (3, "m3", 1, "99.0"),
                ):
                    connection.execute(
                        text(
                            "INSERT INTO student_registrations (id, exam_id, matrikelnummer, "
                            "nachname, vorname, course_code, module_title, versuch, kommentar, "
                            "flagged, excluded, attended, bonus_points, source_filename) VALUES "
                            "(:id, 1, :m, 'N', 'V', 'C', 'Mod', 1, NULL, 0, :excluded, NULL, "
                            ":bonus, NULL)"
                        ),
                        {"id": reg_id, "m": matrikelnummer, "excluded": excluded, "bonus": bonus},
                    )
        finally:
            engine.dispose()

        command.upgrade(config, "head")
    finally:
        get_settings.cache_clear()

    engine = create_engine(url)
    try:
        with engine.connect() as connection:
            registration_count = connection.execute(
                text("SELECT count(*) FROM student_registrations")
            ).scalar_one()
            exam_bonus_points = connection.execute(
                text("SELECT bonus_points FROM exams WHERE id = 1")
            ).scalar_one()
    finally:
        engine.dispose()

    assert registration_count == 3, "the FK-cascade bug deletes every registration on this step"
    assert exam_bonus_points == "2.5"  # max(2.5, 1.0); the excluded student's 99.0 is ignored
