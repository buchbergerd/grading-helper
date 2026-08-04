"""Login, logout, identity and self-service password change (§3, ``docs/api-contract.md``).

The session token only ever appears in a ``Set-Cookie`` header — never in a response body, a log
line or an error message. Same for passwords.
"""

from __future__ import annotations

from typing import Any, cast

from fastapi import APIRouter, HTTPException, Response, status
from sqlalchemy import or_, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError

from app.api.schemas import LoginRequest, PasswordChangeRequest, RegisterRequest, UserIdentity
from app.auth.cookies import clear_session_cookie, set_session_cookie
from app.auth.dependencies import CurrentSession, CurrentUser, DbSession, OptionalSession
from app.auth.passwords import (
    DUMMY_HASH,
    hash_password,
    needs_rehash,
    validate_password_strength,
    verify_password,
)
from app.auth.sessions import as_utc, create_session, delete_all_sessions_for_user, delete_session
from app.models import InvitationCode, User
from app.models.common import utcnow

router = APIRouter(prefix="/auth", tags=["auth"])

#: One message for "no such user", "wrong password" and "account deactivated" (contract: Auth
#: table). Telling them apart would turn the login form into a username oracle and would leak
#: that a named colleague's account has been disabled.
INVALID_CREDENTIALS_DETAIL = "Benutzername oder Passwort ist falsch."
WRONG_CURRENT_PASSWORD_DETAIL = "Das aktuelle Passwort ist falsch."
#: One message for "no such code", "expired", "revoked" and "already redeemed the maximum number
#: of times" — same reasoning as above, and the code space is 256 bits of entropy
#: (``INVITATION_CODE_BYTES``) so there is no realistic enumeration to protect against; this is
#: just consistency, not a security control.
INVALID_INVITATION_DETAIL = (
    "Dieser Einladungscode ist ungültig, abgelaufen, wurde widerrufen oder wurde bereits "
    "die maximale Anzahl an Malen eingelöst."
)
USERNAME_TAKEN_DETAIL = "Dieser Benutzername ist bereits vergeben."


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


@router.post("/register", response_model=UserIdentity, status_code=status.HTTP_201_CREATED)
def register(payload: RegisterRequest, response: Response, db: DbSession) -> User:
    """Create an instructor account by redeeming an admin-issued invitation code (§3).

    Besides ``/login``, the only unauthenticated route that *creates state* — ``/health`` and
    ``/logout`` need no session either, but neither one ever writes a row. Deliberately narrow,
    since §3's default is no public account creation. The invitation code is checked, in full,
    **before** the username is looked at: reversing that order would turn this into a
    username-existence oracle for anyone, code or not, which a colleague already holding a valid
    code does not need.

    A code is reusable — this route does not consume it, only increments its
    ``redemption_count`` — so the same code can be redeemed by any number of colleagues until it
    expires, an admin revokes it, or (if capped) it has reached its ``max_uses`` (e.g. one code
    posted in a group chat). The increment happens via a single atomic ``UPDATE ... WHERE``
    statement whose ``WHERE`` clause re-checks ``max_uses`` server-side
    (``redemption_count < max_uses``), not a Python read-modify-write: two concurrent redemptions
    of the same about-to-be-exhausted code can't both read "one slot left" and both squeeze
    through, pushing the count past the cap. A ``0`` rowcount means the code was consumed out
    from under this request between the lookup above and here — same outcome as any other invalid
    code.

    Always creates a non-admin account; there is no way to request admin rights through a code.
    """
    invitation = db.execute(
        select(InvitationCode).where(InvitationCode.code == payload.code)
    ).scalar_one_or_none()

    now = utcnow()
    if (
        invitation is None
        or invitation.revoked_at is not None
        or as_utc(invitation.expires_at) <= now
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=INVALID_INVITATION_DETAIL
        )

    errors = validate_password_strength(payload.password)
    if errors:
        raise HTTPException(status_code=422, detail={"errors": errors})

    redemption = cast(
        "CursorResult[Any]",
        db.execute(
            update(InvitationCode)
            .where(InvitationCode.id == invitation.id)
            .where(
                or_(
                    InvitationCode.max_uses.is_(None),
                    InvitationCode.redemption_count < InvitationCode.max_uses,
                )
            )
            .values(redemption_count=InvitationCode.redemption_count + 1)
        ),
    )
    if redemption.rowcount == 0:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=INVALID_INVITATION_DETAIL
        )

    user = User(
        username=payload.username,
        password_hash=hash_password(payload.password),
        is_admin=False,
        is_active=True,
    )
    db.add(user)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=USERNAME_TAKEN_DETAIL
        ) from exc

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
