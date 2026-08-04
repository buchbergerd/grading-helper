"""exam statistics share links

Revision ID: 4ab0ab0a96d5
Revises: a2ef145b150f
Create Date: 2026-08-04 19:46:05.313462

SPECIFICATION.md §3's second public-access exception: an owner-generated, revocable token
(``Exam.share_token``) that unlocks the read-only §9 statistics dashboard (``app/api/sharing.py``)
without a session. ``null`` means sharing is off, the default for every existing and new exam.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "4ab0ab0a96d5"
down_revision: str | Sequence[str] | None = "a2ef145b150f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("exams", schema=None) as batch_op:
        batch_op.add_column(sa.Column("share_token", sa.String(length=64), nullable=True))
        batch_op.create_index(batch_op.f("ix_exams_share_token"), ["share_token"], unique=True)


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("exams", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_exams_share_token"))
        batch_op.drop_column("share_token")
