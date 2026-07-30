"""Login, logout, identity and self-service password change (§3, ``docs/api-contract.md``).

The session token only ever appears in a ``Set-Cookie`` header — never in a response body, a log
line or an error message. Same for passwords.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Response, status
from sqlalchemy import select

from app.api.schemas import LoginRequest, PasswordChangeRequest, UserIdentity
from app.auth.cookies import clear_session_cookie, set_session_cookie
from app.auth.dependencies import CurrentSession, CurrentUser, DbSession, OptionalSession
from app.auth.passwords import (
    DUMMY_HASH,
    hash_password,
    needs_rehash,
    validate_password_strength,
    verify_password,
)
from app.auth.sessions import create_session, delete_all_sessions_for_user, delete_session
from app.models import User

router = APIRouter(prefix="/auth", tags=["auth"])

#: One message for "no such user", "wrong password" and "account deactivated" (contract: Auth
#: table). Telling them apart would turn the login form into a username oracle and would leak
#: that a named colleague's account has been disabled.
INVALID_CREDENTIALS_DETAIL = "Benutzername oder Passwort ist falsch."
WRONG_CURRENT_PASSWORD_DETAIL = "Das aktuelle Passwort ist falsch."


@router.post("/login", response_model=UserIdentity)
def login(payload: LoginRequest, response: Response, db: DbSession) -> User:
    """Authenticate and start a session.

    Every failure path performs exactly one argon2 verification — including the unknown-username
    path, which verifies against :data:`~app.auth.passwords.DUMMY_HASH`. Without that, "no such
    user" would answer in microseconds while "wrong password" took the full argon2 work factor,
    and the timing difference alone would enumerate valid usernames.
    """
    user = db.execute(select(User).where(User.username == payload.username)).scalar_one_or_none()

    if user is None:
        verify_password(DUMMY_HASH, payload.password)
        password_ok = False
    else:
        password_ok = verify_password(user.password_hash, payload.password)

    if user is None or not password_ok or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail=INVALID_CREDENTIALS_DETAIL
        )

    # Transparent upgrade if the stored hash predates a parameter bump.
    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(payload.password)
        db.commit()

    session = create_session(db, user)
    set_session_cookie(response, session.token)
    return user


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(session: OptionalSession, response: Response, db: DbSession) -> None:
    """End the current session and clear the cookie.

    Idempotent by design: an already-expired or already-revoked session still returns ``204``
    and still clears the cookie. Answering ``401`` here would strand a useless cookie in the
    browser that the user has no way to get rid of.
    """
    if session is not None:
        delete_session(db, session.token)
    clear_session_cookie(response)


@router.get("/me", response_model=UserIdentity)
def me(user: CurrentUser) -> User:
    """The signed-in account. ``401`` if the cookie is missing, expired or revoked."""
    return user


@router.post("/password", status_code=status.HTTP_204_NO_CONTENT)
def change_password(payload: PasswordChangeRequest, session: CurrentSession, db: DbSession) -> None:
    """Change one's own password.

    Every *other* session of this user is revoked — if the password is being changed because it
    may have leaked, leaving the attacker's session alive would defeat the point. The caller's
    own session survives so the browser they are using does not get logged out.
    """
    user = session.user

    if not verify_password(user.password_hash, payload.current_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail=WRONG_CURRENT_PASSWORD_DETAIL
        )

    errors = validate_password_strength(payload.new_password)
    if errors:
        # 422 + {"detail": {"errors": [...]}} — the contract's shape for German validation
        # messages shown verbatim. Literal 422 because starlette has deprecated the
        # HTTP_422_UNPROCESSABLE_ENTITY constant in favour of a renamed one.
        raise HTTPException(status_code=422, detail={"errors": errors})

    user.password_hash = hash_password(payload.new_password)
    db.commit()
    delete_all_sessions_for_user(db, user.id, except_token=session.token)
