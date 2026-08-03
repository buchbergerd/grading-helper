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

from alembic import command

_ALEMBIC_INI = Path(__file__).resolve().parent.parent / "alembic.ini"


def run_migrations() -> None:
    """Bring the configured database up to the latest schema."""
    command.upgrade(Config(str(_ALEMBIC_INI)), "head")
