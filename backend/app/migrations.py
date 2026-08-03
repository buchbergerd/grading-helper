"""Run Alembic migrations programmatically (docs/open-questions.md #11).

Production's *only* schema-creation/upgrade path is :func:`run_migrations` — ``alembic upgrade
head`` against whatever ``GRADINGHELPER_DATABASE_URL`` points at (``alembic/env.py`` reads the
same ``app.config.get_settings()`` the app itself uses, so there is one source of truth for
"which database"). A brand-new, empty database and a deployment several migrations behind both
converge on the same history instead of the app trusting an implicit "must already be
create_all-shaped" assumption.

``app/db.py::init_db`` (``Base.metadata.create_all``) still exists and is deliberately *not*
replaced by this everywhere: tests use it because they exercise the storage layer itself
(``conftest.py``'s docstring) and want a fresh schema per test without an ``alembic_version``
table or migration history in the way.
"""

from __future__ import annotations

from pathlib import Path

from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from sqlalchemy import inspect

from alembic import command
from app.config import get_settings
from app.db import create_engine_for

_ALEMBIC_INI = Path(__file__).resolve().parent.parent / "alembic.ini"

# The first table the initial migration creates — see alembic/versions/fb8602eb97b1_*.py. Its
# presence is what distinguishes a database that predates Alembic from a genuinely empty one.
_LEGACY_MARKER_TABLE = "users"


def run_migrations() -> None:
    """Bring the configured database up to the latest schema.

    A database that predates Alembic (every environment created via ``app/db.py::init_db``'s
    ``create_all`` before this migration existed — which, concretely, means every developer's
    existing local ``backend/data/gradinghelper.db``) already has the full schema but no
    ``alembic_version`` row. Running ``upgrade`` unconditionally against it would replay the
    initial migration's ``CREATE TABLE`` statements against tables that already exist and fail
    with "table already exists". Since the initial migration's schema is exactly what
    ``create_all`` produces (``tests/test_migrations.py`` asserts they never drift apart), the
    correct fix for that case is to *stamp* the database at head — record it as already there —
    rather than replay DDL it has already effectively applied. A genuinely fresh, empty database
    has neither an ``alembic_version`` row nor ``_LEGACY_MARKER_TABLE``, so it takes the normal
    ``upgrade`` path and gets both created from scratch.
    """
    config = Config(str(_ALEMBIC_INI))
    engine = create_engine_for(get_settings().database_url)
    try:
        with engine.connect() as connection:
            current_revision = MigrationContext.configure(connection).get_current_revision()
            predates_alembic = _LEGACY_MARKER_TABLE in inspect(connection).get_table_names()
    finally:
        engine.dispose()

    if current_revision is None and predates_alembic:
        command.stamp(config, "head")
    else:
        command.upgrade(config, "head")
