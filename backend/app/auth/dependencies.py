"""FastAPI dependencies for authentication and authorization (SPECIFICATION.md §3).

``current_session`` is the primitive: routes that need to keep the caller's own session alive
while revoking their others (the self-service password change) need the token, not just the user.
``current_user`` is defined on top of it so ordinary routes stay simple.

No ``WWW-Authenticate`` header is sent with the 401: this is cookie authentication, and that
header would make browsers pop up a native Basic-auth dialog.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.auth.sessions import get_valid_session
from app.config import get_settings
from app.db import get_db
from app.models import User, UserSession

#: Deliberately identical for "no cookie", "unknown token", "expired" and "account deactivated" —
#: an unauthenticated caller learns nothing from it.
NOT_AUTHENTICATED_DETAIL = "Nicht angemeldet oder Sitzung abgelaufen."
FORBIDDEN_DETAIL = "Diese Aktion erfordert Administratorrechte."

DbSession = Annotated[Session, Depends(get_db)]


def current_session(request: Request, db: DbSession) -> UserSession:
    """Resolve the session cookie to a valid :class:`UserSession`, or raise ``401``."""
    token = request.cookies.get(get_settings().session_cookie_name)
    session = get_valid_session(db, token) if token else None
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail=NOT_AUTHENTICATED_DETAIL
        )
    return session


def optional_current_session(request: Request, db: DbSession) -> UserSession | None:
    """Like :func:`current_session`, but returns ``None`` instead of raising.

    Used by logout, which must stay idempotent: if the session has already expired or been
    revoked, raising ``401`` would leave the now-useless cookie sitting in the browser with no
    way for the user to clear it.
    """
    token = request.cookies.get(get_settings().session_cookie_name)
    return get_valid_session(db, token) if token else None


def current_user(session: Annotated[UserSession, Depends(current_session)]) -> User:
    """The authenticated, active user behind the request's session cookie."""
    return session.user


def require_admin(user: Annotated[User, Depends(current_user)]) -> User:
    """Like :func:`current_user`, but ``403`` for non-admin accounts (§3)."""
    if not user.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=FORBIDDEN_DETAIL)
    return user


CurrentSession = Annotated[UserSession, Depends(current_session)]
OptionalSession = Annotated[UserSession | None, Depends(optional_current_session)]
CurrentUser = Annotated[User, Depends(current_user)]
AdminUser = Annotated[User, Depends(require_admin)]
