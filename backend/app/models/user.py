"""User accounts and login sessions (SPECIFICATION.md §3)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.common import utcnow


class User(Base):
    """An instructor or admin account. Passwords are only ever stored hashed (§3)."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(150), unique=True, index=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    sessions: Mapped[list[UserSession]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    # NOTE: there is deliberately no `lectures` / `exams` relationship here. A default-cascade
    # one-to-many on this side would make session.delete(user) NULL out lectures.owner_id in
    # Python, defeating the RESTRICT foreign key that is supposed to make deleting a user who
    # still owns lectures fail loudly instead of orphaning exam data. Navigate the other way
    # (Lecture.owner) or query lectures by owner_id explicitly.

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<User id={self.id} username={self.username!r} is_admin={self.is_admin}>"


class UserSession(Base):
    """An opaque server-side session token issued at login (§3)."""

    __tablename__ = "user_sessions"

    token: Mapped[str] = mapped_column(String(128), primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    user: Mapped[User] = relationship(back_populates="sessions")

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<UserSession user_id={self.user_id} expires_at={self.expires_at}>"
