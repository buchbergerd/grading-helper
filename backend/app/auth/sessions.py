"""Server-side session tokens (SPECIFICATION.md §3).

Sessions are opaque random tokens stored in the database, **not** JWTs: §3 requires account
deactivation and password reset to take effect immediately, which is only possible if the server
can revoke an already-issued credential. Every function here is a plain SQLAlchemy operation so
the revocation paths (:func:`delete_all_sessions_for_user`) are cheap and obvious.

Timezone handling: ``UserSession.expires_at`` is written as a timezone-aware UTC datetime, but
SQLite has no datetime type — the value is stored as a string and SQLAlchemy's SQLite dialect
returns it **naive** on read. Comparing that naive value against :func:`~app.models.common.utcnow`
raises ``TypeError: can't compare offset-naive and offset-aware datetimes``, which would take the
whole app down the first time a session was validated in a fresh process. :func:`as_utc`
normalizes explicitly; the expiry comparison is done in Python rather than in SQL for the same
reason (a SQL-side comparison would compare strings with and without an offset suffix).
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from sqlalchemy import delete, select
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import User, UserSession
from app.models.common import utcnow

#: Bytes of entropy per token. ``token_urlsafe(32)`` yields a 43-character string.
TOKEN_BYTES = 32


def as_utc(value: datetime) -> datetime:
    """Return ``value`` as a timezone-aware UTC datetime.

    A naive value is *assumed* to be UTC — which it is, because everything written to these
    columns comes from :func:`~app.models.common.utcnow`; it only lost its ``tzinfo`` crossing
    the SQLite boundary.
    """
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def session_lifetime_seconds() -> int:
    """Absolute session lifetime in seconds (also used for the cookie's ``max_age``)."""
    return get_settings().session_lifetime_hours * 3600


def create_session(db: Session, user: User) -> UserSession:
    """Issue a new session for ``user`` and commit it.

    The returned object carries the plaintext token in ``.token``; it is only ever put into the
    ``Set-Cookie`` header, never into a response body or a log line.
    """
    now = utcnow()
    session = UserSession(
        token=secrets.token_urlsafe(TOKEN_BYTES),
        user_id=user.id,
        created_at=now,
        expires_at=now + timedelta(hours=get_settings().session_lifetime_hours),
    )
    db.add(session)
    db.commit()
    return session


def get_valid_session(db: Session, token: str) -> UserSession | None:
    """Return the session for ``token``, or ``None`` if it cannot be used.

    ``None`` covers all three rejection cases without distinguishing them to the caller: no such
    token, the token has expired, or the owning account has been deactivated (§3 — deactivation
    must take effect immediately, including for sessions issued before it).

    A session that is still valid has its ``expires_at`` pushed out to a fresh full lifetime from
    *now* (§12: a sliding window, "24h that refresh on activity", not a fixed 24h-from-login
    expiry) — every validated request counts as activity, so an instructor working continuously
    is never logged out mid-session. :func:`~app.auth.dependencies.current_session` refreshes the
    cookie's ``max_age`` to match on the same request.
    """
    if not token:
        return None
    session = db.get(UserSession, token)
    if session is None:
        return None
    if as_utc(session.expires_at) <= utcnow():
        return None
    user = db.get(User, session.user_id)
    if user is None or not user.is_active:
        return None
    session.expires_at = utcnow() + timedelta(hours=get_settings().session_lifetime_hours)
    db.commit()
    return session


def delete_session(db: Session, token: str) -> None:
    """Delete a single session (logout). A token that no longer exists is not an error."""
    db.execute(delete(UserSession).where(UserSession.token == token))
    db.commit()


def delete_all_sessions_for_user(
    db: Session, user_id: int, *, except_token: str | None = None
) -> int:
    """Revoke every session of ``user_id``; return how many were deleted.

    ``except_token`` keeps one session alive — used by the self-service password change, where
    logging the user out of the browser they just used would be gratuitous. Deactivation and
    admin password reset pass nothing and revoke everything.
    """
    statement = delete(UserSession).where(UserSession.user_id == user_id)
    if except_token is not None:
        statement = statement.where(UserSession.token != except_token)
    # Session.execute() is typed as returning Result, which has no rowcount; a DML statement
    # always yields a CursorResult at runtime.
    result = cast("CursorResult[Any]", db.execute(statement))
    db.commit()
    return result.rowcount


def purge_expired_sessions(db: Session) -> int:
    """Delete every session whose ``expires_at`` has passed; return how many.

    Housekeeping only — :func:`get_valid_session` already refuses expired tokens, so this never
    affects authorization. Compares in Python (see the module docstring on naive SQLite reads).
    """
    now = utcnow()
    expired = [
        token
        for token, expires_at in db.execute(select(UserSession.token, UserSession.expires_at)).all()
        if as_utc(expires_at) <= now
    ]
    if not expired:
        return 0
    db.execute(delete(UserSession).where(UserSession.token.in_(expired)))
    db.commit()
    return len(expired)
