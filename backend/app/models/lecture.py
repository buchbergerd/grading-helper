"""Lecture — a recurring course grouping its exam sittings over time (SPECIFICATION.md §4)."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.common import utcnow
from app.models.user import User

if TYPE_CHECKING:
    from app.models.exam import Exam


class Lecture(Base):
    """An internal organisational label chosen by the instructor.

    Per §4 the lecture name is *never* derived from or validated against a registration PDF's
    title line — its only job is to group an exam's recurring sittings so settings can be copied
    forward when a new exam is created.
    """

    __tablename__ = "lectures"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    # RESTRICT, not CASCADE: deleting a user who still owns lectures must fail loudly rather
    # than destroying exam data. Exam deletion is an explicit, deliberate action (§13).
    owner_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    owner: Mapped[User] = relationship()
    exams: Mapped[list[Exam]] = relationship(
        back_populates="lecture",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="Exam.id",
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Lecture id={self.id} name={self.name!r}>"
