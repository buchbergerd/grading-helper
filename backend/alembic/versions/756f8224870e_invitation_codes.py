"""invitation codes

Revision ID: 756f8224870e
Revises: 8ab4f293a4ce
Create Date: 2026-08-04 12:58:16.233868

SPECIFICATION.md §3: admin-issued, reusable, time-limited codes that let a prospective instructor
create their own account (``POST /api/auth/register``) instead of an admin creating it for them
via ``POST /api/admin/users``. A code stops working once it expires or an admin revokes it; there
is no per-redemption limit, so one code can be shared with a whole team.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "756f8224870e"
down_revision: str | Sequence[str] | None = "8ab4f293a4ce"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "invitation_codes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("created_by_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("redemption_count", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("invitation_codes", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_invitation_codes_code"), ["code"], unique=True)
        batch_op.create_index(
            batch_op.f("ix_invitation_codes_created_by_id"), ["created_by_id"], unique=False
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("invitation_codes", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_invitation_codes_created_by_id"))
        batch_op.drop_index(batch_op.f("ix_invitation_codes_code"))

    op.drop_table("invitation_codes")
