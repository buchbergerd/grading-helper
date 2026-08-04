"""Admin-issued invitation codes for self-service account creation (SPECIFICATION.md §3).

A code is a reusable, time-limited credential an admin hands to prospective instructors out of
band (e.g. posted in a group chat so a whole team can join with one link). Redeeming it
(``POST /api/auth/register``) is the one exception to §3's "no public/anonymous access": anyone
holding a valid code can create their own account, always as a non-admin instructor — promotion
to admin still requires an existing admin via ``PATCH /api/admin/users/{id}``, matching the
least-privilege default elsewhere in this app. A code stops working once it expires or an admin
revokes it; there is no per-redemption limit.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.common import utcnow
from app.models.user import User


class InvitationCode(Base):
    """One admin-issued invitation code.

    ``revoked_at`` is set at most once and never cleared. ``redemption_count`` is incremented
    SQL-side on every successful registration (``InvitationCode.redemption_count + 1``, not a
    read-modify-write in Python) so concurrent redemptions never lose a count. There is no
    per-redeemer record: with one code shared department-wide, the users list (with its
    ``created_at``) already is the roster of who joined via it.
    """

    __tablename__ = "invitation_codes"

    id: Mapped[int] = mapped_column(primary_key=True)
    #: Opaque random token, looked up as-is (not hashed) — same choice as ``UserSession.token``.
    code: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    created_by_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    redemption_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # Deliberately no back-reference collection on User — see that model's own docstring on why
    # an extra relationship there is hazardous around session.delete(user).
    created_by: Mapped[User] = relationship()

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<InvitationCode id={self.id} redemption_count={self.redemption_count}>"
