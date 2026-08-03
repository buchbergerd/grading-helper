"""bonus points per exam

Revision ID: 8ab4f293a4ce
Revises: fb8602eb97b1
Create Date: 2026-08-03 18:02:44.450284

SPECIFICATION.md §7.3 changed: ``bonus_points`` moves from ``StudentRegistration`` to ``Exam`` —
one amount per exam, applied identically to every non-excluded student, rather than one value per
student. The data step below carries each exam's most generous *previously entered* per-student
value forward as its new single value, rather than defaulting every exam to 0: in practice the
points-entry UI already presented bonus as one shared field fanned out to every row (with a
"uneinheitlich" hint on disagreement), so existing data is expected to already be unanimous per
exam, and the max is a safe, intent-preserving choice on the rare row that drifted. Excluded
students' old values are ignored — they never contributed a grade to begin with (§5.3).
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal

import sqlalchemy as sa

import app.types
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "8ab4f293a4ce"
down_revision: str | Sequence[str] | None = "fb8602eb97b1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    bind = op.get_bind()

    with op.batch_alter_table("exams", schema=None) as batch_op:
        batch_op.add_column(sa.Column("bonus_points", app.types.DecimalText(), nullable=True))

    # Per exam, carry forward the highest previously entered non-excluded student value — see
    # this migration's module docstring for why "highest" rather than "zero" or "first".
    rows = bind.execute(
        sa.text("SELECT exam_id, bonus_points FROM student_registrations WHERE excluded = 0")
    ).all()
    highest_by_exam: dict[int, Decimal] = {}
    for exam_id, bonus_points_text in rows:
        value = Decimal(bonus_points_text)
        current = highest_by_exam.get(exam_id)
        if current is None or value > current:
            highest_by_exam[exam_id] = value

    for exam_id, value in highest_by_exam.items():
        bind.execute(
            sa.text("UPDATE exams SET bonus_points = :value WHERE id = :exam_id"),
            {"value": str(value), "exam_id": exam_id},
        )
    bind.execute(sa.text("UPDATE exams SET bonus_points = '0' WHERE bonus_points IS NULL"))

    with op.batch_alter_table("exams", schema=None) as batch_op:
        batch_op.alter_column("bonus_points", nullable=False)

    with op.batch_alter_table("student_registrations", schema=None) as batch_op:
        batch_op.drop_column("bonus_points")


def downgrade() -> None:
    """Downgrade schema.

    The exam-wide amount is written back onto every non-excluded student as their per-student
    value (matching what the pre-M6.1 UI would have shown for a freshly-migrated exam); excluded
    students and any exam with no registrations yet get nothing to backfill.
    """
    with op.batch_alter_table("student_registrations", schema=None) as batch_op:
        batch_op.add_column(sa.Column("bonus_points", sa.TEXT(), nullable=True))

    bind = op.get_bind()
    bind.execute(
        sa.text(
            "UPDATE student_registrations "
            "SET bonus_points = (SELECT exams.bonus_points FROM exams "
            "WHERE exams.id = student_registrations.exam_id) "
            "WHERE excluded = 0"
        )
    )
    bind.execute(
        sa.text("UPDATE student_registrations SET bonus_points = '0' WHERE bonus_points IS NULL")
    )

    with op.batch_alter_table("student_registrations", schema=None) as batch_op:
        batch_op.alter_column("bonus_points", nullable=False)

    with op.batch_alter_table("exams", schema=None) as batch_op:
        batch_op.drop_column("bonus_points")
