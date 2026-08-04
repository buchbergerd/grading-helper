"""invitation code max uses

Revision ID: a2ef145b150f
Revises: 756f8224870e
Create Date: 2026-08-04 15:52:47.758001

SPECIFICATION.md §3: an admin can optionally cap how many times an invitation code may be
redeemed, alongside the existing time-based expiry. ``NULL`` (the value every pre-existing row
gets, no backfill needed) means unlimited — the original behaviour.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a2ef145b150f"
down_revision: str | Sequence[str] | None = "756f8224870e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("invitation_codes", schema=None) as batch_op:
        batch_op.add_column(sa.Column("max_uses", sa.Integer(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("invitation_codes", schema=None) as batch_op:
        batch_op.drop_column("max_uses")
