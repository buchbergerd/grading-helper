"""Exam, its exercises and its grading schema (SPECIFICATION.md §4, §7)."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import TYPE_CHECKING

from sqlalchemy import Date, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.common import utcnow
from app.models.lecture import Lecture
from app.models.user import User
from app.types import DecimalText

if TYPE_CHECKING:
    from app.models.registration import StudentRegistration


class BonusMode(StrEnum):
    """How bonus points are applied to a student's total (§7.3).

    ``ALWAYS``
        ``final_total = raw_total + bonus_points``, uncapped.
    ``ONLY_IF_PASSING_WITHOUT_BONUS``
        Bonus is applied only if ``raw_total`` *alone* already meets the 4.0 threshold; bonus
        points can improve a grade but never turn a fail into a pass.

    Member names and values are kept identical because ``SAEnum`` persists the member *name*.
    """

    ALWAYS = "ALWAYS"
    ONLY_IF_PASSING_WITHOUT_BONUS = "ONLY_IF_PASSING_WITHOUT_BONUS"


class Exam(Base):
    """One concrete exam sitting, e.g. "WiSe 23/24, 1. Termin"."""

    __tablename__ = "exams"

    id: Mapped[int] = mapped_column(primary_key=True)
    lecture_id: Mapped[int] = mapped_column(
        ForeignKey("lectures.id", ondelete="CASCADE"), index=True, nullable=False
    )
    # §4: the exam's owner defaults to the lecture's owner at creation time but is editable
    # afterwards, so it is stored independently and never derived from Lecture.owner_id.
    owner_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True, nullable=False
    )
    semester: Mapped[str] = mapped_column(String(64), nullable=False)
    termin: Mapped[str] = mapped_column(String(64), nullable=False)
    exam_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    bonus_mode: Mapped[BonusMode] = mapped_column(
        SAEnum(BonusMode, native_enum=False, validate_strings=True, length=32),
        default=BonusMode.ALWAYS,
        nullable=False,
    )
    # One amount for the whole exam (§7.3), applied identically to every non-excluded student —
    # not per student. DecimalText, never Numeric/Float — see app/types.py and §7.0.
    bonus_points: Mapped[Decimal] = mapped_column(DecimalText, default=Decimal(0), nullable=False)
    # §3's second public-access exception (app/api/sharing.py): an owner-generated, revocable
    # token that unlocks the read-only §9 statistics dashboard without a session. `None` means
    # sharing is off (the default). Looked up directly by value, like `InvitationCode.code` and
    # `UserSession.token` — not a relationship.
    share_token: Mapped[str | None] = mapped_column(
        String(64), unique=True, index=True, nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    lecture: Mapped[Lecture] = relationship(back_populates="exams")
    owner: Mapped[User] = relationship()

    exercises: Mapped[list[Exercise]] = relationship(
        back_populates="exam",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="Exercise.position",
    )
    grade_thresholds: Mapped[list[GradeThreshold]] = relationship(
        back_populates="exam",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    registrations: Mapped[list[StudentRegistration]] = relationship(
        back_populates="exam",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Exam id={self.id} semester={self.semester!r} termin={self.termin!r}>"


class Exercise(Base):
    """One exercise (Aufgabe) of an exam, with its maximum attainable points."""

    __tablename__ = "exercises"
    __table_args__ = (UniqueConstraint("exam_id", "position", name="uq_exercise_exam_position"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    exam_id: Mapped[int] = mapped_column(
        ForeignKey("exams.id", ondelete="CASCADE"), index=True, nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    # DecimalText, never Numeric/Float — see app/types.py and §7.0.
    max_points: Mapped[Decimal] = mapped_column(DecimalText, nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)

    exam: Mapped[Exam] = relationship(back_populates="exercises")

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Exercise id={self.id} name={self.name!r} max_points={self.max_points}>"


class GradeThreshold(Base):
    """The required percentage for one of the ten passing grades of an exam (§7.1, §7.2).

    ``grade`` is stored as a *string* ("1.0", "1.3", ...) — never a float. It is a label on the
    German grade scale, and float round-tripping of e.g. 1.3 is exactly what §7.0 forbids.
    """

    __tablename__ = "grade_thresholds"
    __table_args__ = (UniqueConstraint("exam_id", "grade", name="uq_grade_threshold_exam_grade"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    exam_id: Mapped[int] = mapped_column(
        ForeignKey("exams.id", ondelete="CASCADE"), index=True, nullable=False
    )
    grade: Mapped[str] = mapped_column(String(8), nullable=False)
    percentage: Mapped[Decimal] = mapped_column(DecimalText, nullable=False)

    exam: Mapped[Exam] = relationship(back_populates="grade_thresholds")

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<GradeThreshold exam_id={self.exam_id} grade={self.grade!r} pct={self.percentage}>"
