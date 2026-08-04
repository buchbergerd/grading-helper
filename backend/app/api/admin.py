"""Account management — admin only (§3, ``docs/api-contract.md``).

Every route here touches **only** the ``users``, ``user_sessions`` and ``invitation_codes``
tables. Per §14 #5 the admin role is account management, not a support back door into other
instructors' exam data (names, Matrikelnummern, grades) — do not add a lectures/exams read path
to this module. Invitation codes belong here for the same reason password reset does: they
create/gate *accounts*, not exam data — redemption itself (``POST /api/auth/register``) lives in
``app.api.auth`` because it is the only unauthenticated route that creates state (``/health`` and
``/logout`` need no session either, but neither one ever writes a row).
"""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta
from typing import Literal

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.api.schemas import (
    InvitationOut,
    PasswordResetRequest,
    UserAccount,
    UserCreateRequest,
    UserUpdateRequest,
)
from app.auth.dependencies import AdminUser, DbSession
from app.auth.passwords import hash_password, validate_password_strength
from app.auth.sessions import as_utc, delete_all_sessions_for_user
from app.config import get_settings
from app.models import InvitationCode, User
from app.models.common import utcnow

router = APIRouter(prefix="/admin", tags=["admin"])

USER_NOT_FOUND_DETAIL = "Benutzerkonto nicht gefunden."
USERNAME_TAKEN_DETAIL = "Dieser Benutzername ist bereits vergeben."
SELF_DEACTIVATE_DETAIL = "Das eigene Konto kann nicht deaktiviert werden."
SELF_DEMOTE_DETAIL = "Die eigenen Administratorrechte können nicht entzogen werden."
INVITATION_NOT_FOUND_DETAIL = "Einladungscode nicht gefunden."
#: Bytes of entropy for an invitation code — same generous margin as a session token
#: (``app.auth.sessions.TOKEN_BYTES``); this is a credential that creates an account, not just
#: one that identifies an existing session, so it gets no less.
INVITATION_CODE_BYTES = 32


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
    """Create an instructor or admin account directly.

    The other way to get an instructor account is self-registration via an invitation code
    issued below (§3) — that path never grants admin, so this route is still the only way to
    create an admin account or to skip the invitation step entirely.
    """
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


# --------------------------------------------------------------------------------------------
# Invitation codes (§3) — self-service account creation, gated by an admin-issued code
# --------------------------------------------------------------------------------------------


def _get_invitation_or_404(db: DbSession, invitation_id: int) -> InvitationCode:
    invitation = db.get(InvitationCode, invitation_id)
    if invitation is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=INVITATION_NOT_FOUND_DETAIL
        )
    return invitation


def _invitation_status(
    invitation: InvitationCode, now: datetime
) -> Literal["active", "expired", "revoked"]:
    """One of ``active``/``revoked``/``expired`` — see :class:`InvitationOut`.

    A code is reusable, so there is no "used" state to report here: redemption is tracked as a
    count, not a terminal status.
    """
    if invitation.revoked_at is not None:
        return "revoked"
    if as_utc(invitation.expires_at) <= now:
        return "expired"
    return "active"


def _to_invitation_out(invitation: InvitationCode, now: datetime) -> InvitationOut:
    return InvitationOut(
        id=invitation.id,
        code=invitation.code,
        created_at=invitation.created_at,
        expires_at=invitation.expires_at,
        created_by=invitation.created_by.username,
        revoked_at=invitation.revoked_at,
        redemption_count=invitation.redemption_count,
        status=_invitation_status(invitation, now),
    )


@router.get("/invitations", response_model=list[InvitationOut])
def list_invitations(admin: AdminUser, db: DbSession) -> list[InvitationOut]:
    """Every invitation code ever issued, newest first — an audit trail, not just "pending"."""
    now = utcnow()
    invitations = (
        db.execute(select(InvitationCode).order_by(InvitationCode.created_at.desc()))
        .scalars()
        .all()
    )
    return [_to_invitation_out(invitation, now) for invitation in invitations]


@router.post("/invitations", response_model=InvitationOut, status_code=status.HTTP_201_CREATED)
def create_invitation(admin: AdminUser, db: DbSession) -> InvitationOut:
    """Issue a new invitation code, valid for the configured lifetime (default 7 days, §3).

    The code is reusable — any number of accounts can be created with it until it expires or an
    admin revokes it, so one code can be shared with a whole team at once (e.g. posted in a group
    chat). It is returned in full so the admin can copy a registration link — it is not a secret
    the app protects on the admin's behalf the way a password is, only a time-limited
    account-creation credential the admin already holds by having just created it.
    """
    now = utcnow()
    invitation = InvitationCode(
        code=secrets.token_urlsafe(INVITATION_CODE_BYTES),
        created_by_id=admin.id,
        created_at=now,
        expires_at=now + timedelta(days=get_settings().invitation_lifetime_days),
    )
    db.add(invitation)
    db.commit()
    return _to_invitation_out(invitation, now)


@router.delete("/invitations/{invitation_id}", status_code=status.HTTP_204_NO_CONTENT)
def revoke_invitation(invitation_id: int, admin: AdminUser, db: DbSession) -> None:
    """Revoke an invitation code so it can no longer be redeemed, however many times it already
    has been.

    Idempotent, like logout: revoking an already-revoked code still returns ``204`` rather than
    an error — the caller's goal ("this code must not work") is already true.
    """
    invitation = _get_invitation_or_404(db, invitation_id)
    if invitation.revoked_at is None:
        invitation.revoked_at = utcnow()
        db.commit()
