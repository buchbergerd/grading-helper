"""Account management — admin only (§3, ``docs/api-contract.md``).

Every route here touches **only** the ``users`` and ``user_sessions`` tables. Per §14 #5 the
admin role is account management, not a support back door into other instructors' exam data
(names, Matrikelnummern, grades) — do not add a lectures/exams read path to this module.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.api.schemas import (
    PasswordResetRequest,
    UserAccount,
    UserCreateRequest,
    UserUpdateRequest,
)
from app.auth.dependencies import AdminUser, DbSession
from app.auth.passwords import hash_password, validate_password_strength
from app.auth.sessions import delete_all_sessions_for_user
from app.models import User

router = APIRouter(prefix="/admin", tags=["admin"])

USER_NOT_FOUND_DETAIL = "Benutzerkonto nicht gefunden."
USERNAME_TAKEN_DETAIL = "Dieser Benutzername ist bereits vergeben."
SELF_DEACTIVATE_DETAIL = "Das eigene Konto kann nicht deaktiviert werden."
SELF_DEMOTE_DETAIL = "Die eigenen Administratorrechte können nicht entzogen werden."


def _require_acceptable_password(password: str) -> None:
    """Raise ``422`` with German messages if ``password`` fails the policy (§3).

    Shape borrowed from the contract's grading-schema validation errors
    (``{"detail": {"errors": [...]}}``) so the frontend has one renderer for German validation
    messages. Literal 422 because starlette has deprecated the ``HTTP_422_UNPROCESSABLE_ENTITY``
    constant in favour of a renamed one.
    """
    errors = validate_password_strength(password)
    if errors:
        raise HTTPException(status_code=422, detail={"errors": errors})


def _get_user_or_404(db: DbSession, user_id: int) -> User:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=USER_NOT_FOUND_DETAIL)
    return user


@router.get("/users", response_model=list[UserAccount])
def list_users(admin: AdminUser, db: DbSession) -> list[User]:
    """All accounts, ordered by username."""
    return list(db.execute(select(User).order_by(User.username)).scalars().all())


@router.post("/users", response_model=UserAccount, status_code=status.HTTP_201_CREATED)
def create_user(payload: UserCreateRequest, admin: AdminUser, db: DbSession) -> User:
    """Create an instructor or admin account (§3 — there is no self-signup)."""
    _require_acceptable_password(payload.password)

    user = User(
        username=payload.username,
        password_hash=hash_password(payload.password),
        is_admin=payload.is_admin,
        is_active=True,
    )
    db.add(user)
    try:
        db.commit()
    except IntegrityError as exc:
        # Rely on the UNIQUE index rather than a pre-flight SELECT: the SELECT would be a
        # time-of-check/time-of-use race between two concurrent admins.
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=USERNAME_TAKEN_DETAIL
        ) from exc
    return user


@router.patch("/users/{user_id}", response_model=UserAccount)
def update_user(user_id: int, payload: UserUpdateRequest, admin: AdminUser, db: DbSession) -> User:
    """Activate/deactivate an account or grant/revoke the admin role.

    An admin may not deactivate or demote *themselves*: with a single admin account — the normal
    case for a department tool — either action would lock account management out of the app
    entirely, recoverable only by running ``scripts/create_admin.py`` on the server.

    Deactivation deletes the account's sessions, so it takes effect on the next request rather
    than whenever the victim's 24 h sliding-expiry cookie would otherwise expire (§3).
    """
    user = _get_user_or_404(db, user_id)

    if user.id == admin.id:
        if payload.is_active is False:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail=SELF_DEACTIVATE_DETAIL
            )
        if payload.is_admin is False:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=SELF_DEMOTE_DETAIL)

    deactivating = payload.is_active is False and user.is_active

    if payload.is_active is not None:
        user.is_active = payload.is_active
    if payload.is_admin is not None:
        user.is_admin = payload.is_admin
    db.commit()

    if deactivating:
        delete_all_sessions_for_user(db, user.id)

    return user


@router.post("/users/{user_id}/password", status_code=status.HTTP_204_NO_CONTENT)
def reset_password(
    user_id: int, payload: PasswordResetRequest, admin: AdminUser, db: DbSession
) -> None:
    """Set a new password for an account and revoke all of its sessions.

    Revocation is the point: a reset happens because the old credential is considered
    compromised or lost, so any session issued under it must stop working immediately (§3).
    """
    user = _get_user_or_404(db, user_id)
    _require_acceptable_password(payload.new_password)

    user.password_hash = hash_password(payload.new_password)
    db.commit()
    delete_all_sessions_for_user(db, user.id)
