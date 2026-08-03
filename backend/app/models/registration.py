"""Student registrations and their per-exercise points (SPECIFICATION.md §4, §5, §8)."""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.exam import Exam, Exercise
from app.types import DecimalText


class StudentRegistration(Base):
    """One student registered for one exam, imported from a PDF or added manually (§5).

    ``excluded`` is a flag, never a deletion (§5.3): an excluded student stays in the database
    so the decision and the source data remain auditable, but is omitted from the attendance
    list, from points entry and from every generated report.
    """

    __tablename__ = "student_registrations"
    __table_args__ = (
        UniqueConstraint("exam_id", "matrikelnummer", name="uq_registration_exam_matrikelnummer"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    exam_id: Mapped[int] = mapped_column(
        ForeignKey("exams.id", ondelete="CASCADE"), index=True, nullable=False
    )
    matrikelnummer: Mapped[str] = mapped_column(String(64), nullable=False)
    nachname: Mapped[str] = mapped_column(String(255), nullable=False)
    vorname: Mapped[str] = mapped_column(String(255), nullable=False)
    # Short parenthetical label used for grouping/sorting (§5.1). Free text in v1 — no canonical
    # Studiengang registry (§14.3).
    course_code: Mapped[str] = mapped_column(String(255), nullable=False)
    # The entire title line of the source PDF, stored verbatim and never normalised or
    # cross-checked against other files of the same exam (§4, §5.1) — a Kombinationsprüfung
    # legitimately carries a different module name/CP/BPO version per course.
    module_title: Mapped[str] = mapped_column(Text, nullable=False)
    versuch: Mapped[int] = mapped_column(Integer, nullable=False)
    kommentar: Mapped[str | None] = mapped_column(Text, nullable=True)
    flagged: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    excluded: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Nullable on purpose (§4): NULL means "attendance not yet recorded", which the §8.1
    # completeness gate must be able to distinguish from an explicit False ("nicht erschienen").
    # Therefore no default of any kind here.
    attended: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    source_filename: Mapped[str | None] = mapped_column(String(512), nullable=True)

    exam: Mapped[Exam] = relationship(back_populates="registrations")
    exercise_points: Mapped[list[ExercisePoints]] = relationship(
        back_populates="registration",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<StudentRegistration id={self.id} exam_id={self.exam_id}>"


class ExercisePoints(Base):
    """The points a student scored on one exercise.

    **The absence of a row means "not entered"; a row holding ``Decimal("0")`` means "entered
    zero".** §8.1 forbids conflating the two — the completeness gate must refuse to generate a
    report when points are merely missing, and must never substitute an implicit zero. Hence
    ``points`` is NOT NULL and has *no* column default: a row can only come into existence by
    someone entering a value.
    """

    __tablename__ = "exercise_points"
    __table_args__ = (
        UniqueConstraint("registration_id", "exercise_id", name="uq_points_registration_exercise"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    registration_id: Mapped[int] = mapped_column(
        ForeignKey("student_registrations.id", ondelete="CASCADE"), index=True, nullable=False
    )
    exercise_id: Mapped[int] = mapped_column(
        ForeignKey("exercises.id", ondelete="CASCADE"), index=True, nullable=False
    )
    points: Mapped[Decimal] = mapped_column(DecimalText, nullable=False)

    registration: Mapped[StudentRegistration] = relationship(back_populates="exercise_points")
    # Many-to-one only. There is deliberately no delete-orphan collection on Exercise: this row
    # is owned by the registration, and giving it two cascading parents has surprising ORM
    # semantics. Deleting an exercise relies on the database-level ON DELETE CASCADE instead.
    exercise: Mapped[Exercise] = relationship()

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"<ExercisePoints registration_id={self.registration_id} "
            f"exercise_id={self.exercise_id} points={self.points}>"
        )
