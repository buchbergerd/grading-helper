"""Session-cookie attributes (SPECIFICATION.md §3, §12).

Shared by the login/logout routes and by :func:`app.auth.dependencies.current_session`, which
re-sets the cookie on every authenticated request so its ``max_age`` slides forward in lockstep
with the server-side ``expires_at`` refresh in :func:`app.auth.sessions.get_valid_session`.
"""

from __future__ import annotations

from fastapi import Response

from app.auth.sessions import session_lifetime_seconds
from app.config import get_settings


def set_session_cookie(response: Response, token: str) -> None:
    """Attach (or refresh) the session cookie.

    ``httponly`` keeps the token out of reach of any JavaScript (the frontend never reads it);
    ``samesite="lax"`` blocks cross-site POSTs from carrying it, which is this milestone's CSRF
    defence; ``secure`` follows the deployment (plain HTTP behind the department's TLS-
    terminating proxy by default — see ``app/config.py``).
    """
    response.set_cookie(
        key=get_settings().session_cookie_name,
        value=token,
        max_age=session_lifetime_seconds(),
        httponly=True,
        samesite="lax",
        secure=get_settings().cookie_secure,
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    """Expire the session cookie. Attributes must match :func:`set_session_cookie` to match."""
    response.delete_cookie(
        key=get_settings().session_cookie_name,
        httponly=True,
        samesite="lax",
        secure=get_settings().cookie_secure,
        path="/",
    )
