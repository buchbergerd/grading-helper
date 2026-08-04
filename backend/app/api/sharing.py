"""Statistics share links (SPECIFICATION.md §3's second public-access exception, §9).

An instructor can opt one exam into a **read-only**, unauthenticated view of its §9 statistics
dashboard by generating a share link — a random unguessable token stored on ``Exam.share_token``
(``None`` means sharing is off, the default). Two owner-only routes below mint/revoke it, exactly
like every other exam mutation (``get_owned_exam``'s 404-not-403 posture). The third route,
``GET /api/public/statistics/{token}``, is the **only** unauthenticated route this module adds,
and deliberately the only thing a token unlocks: it serves precisely
:func:`~app.statistics.build_exam_statistics`'s payload, which carries no student names or
Matrikelnummern (see that module's ``ExamStatistics`` docstring) — never a report, a registration
list, or a points-entry endpoint. Revoking the link, or generating a new one, invalidates the old
token immediately, since lookup is by exact value against whatever ``Exam.share_token`` currently
holds.

§9's "accepted, not mitigated" note applies here: a share link to a very small cohort effectively
publishes those students' individual grades within the aggregate. No minimum-N gate exists —
sharing is an explicit, revocable, per-exam instructor choice, not an automatic default (see
``docs/open-questions.md`` item 25).
"""

from __future__ import annotations

import secrets
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.exams import exam_detail, get_owned_exam
from app.api.schemas import DecimalString, ExamDetail
from app.auth.dependencies import CurrentUser, DbSession
from app.models import Exam
from app.statistics import ExamStatistics, build_exam_statistics

router = APIRouter(tags=["sharing"])

#: Bytes of entropy per token — same as ``UserSession``'s (`app/auth/sessions.py`).
TOKEN_BYTES = 32

SHARE_LINK_INVALID_DETAIL = "Dieser Link ist nicht mehr gültig."


@router.post("/exams/{exam_id}/share-link", response_model=ExamDetail)
def create_share_link(exam_id: int, user: CurrentUser, db: DbSession) -> ExamDetail:
    """Mint a new share token for the exam, replacing any existing one (owner-only).

    Always regenerates — there is no "return the existing token" mode. The frontend button reads
    "Link erstellen" the first time and "Neuen Link erstellen" afterwards, but both hit this same
    route; a fresh token invalidates whatever the old one was handed out to, which is the point of
    offering a regenerate action at all (a leaked or over-shared link needs a way out short of
    revoking sharing entirely).
    """
    exam = get_owned_exam(db, user, exam_id)
    exam.share_token = secrets.token_urlsafe(TOKEN_BYTES)
    db.commit()
    db.refresh(exam)
    return exam_detail(db, exam)


@router.delete("/exams/{exam_id}/share-link", status_code=status.HTTP_204_NO_CONTENT)
def revoke_share_link(exam_id: int, user: CurrentUser, db: DbSession) -> None:
    """Turn sharing off (owner-only). Idempotent: already-off still ``204``s."""
    exam = get_owned_exam(db, user, exam_id)
    exam.share_token = None
    db.commit()


def _get_exam_by_share_token(db: Session, token: str) -> Exam:
    """The exam a share token currently unlocks, or ``404`` with one message for every rejection
    reason (unknown, never issued, revoked, regenerated-away) — same posture as
    ``app.auth.sessions.get_valid_session``: an anonymous caller learns nothing from the response
    about which of those applies.
    """
    exam = db.execute(select(Exam).where(Exam.share_token == token)).scalar_one_or_none()
    if exam is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=SHARE_LINK_INVALID_DETAIL)
    return exam


@router.get("/public/statistics/{token}", response_model=None)
def shared_statistics(
    token: str,
    db: DbSession,
    response: Response,
    bonus_points_override: Annotated[DecimalString | None, Query()] = None,
) -> ExamStatistics:
    """The unauthenticated read of an exam's §9 statistics — the one thing a share token unlocks.

    Mirrors ``app.api.statistics.exam_statistics`` (same payload, same ``bonus_points_override``
    simulation, same ``response_model=None`` reasoning: the dict is already the exact wire shape,
    and routing it through pydantic could coerce a stray ``float`` into a plausible-looking
    string). It differs in exactly two ways, both because this route has no session to key off of:
    the exam is looked up by ``share_token`` instead of ownership, and the response is marked
    ``Cache-Control: no-store`` — a public, unauthenticated URL is exactly what an intermediate
    cache would otherwise be tempted to keep (the internal-report PDF sets the same header for the
    same reason, ``app/api/reports.py``).
    """
    response.headers["Cache-Control"] = "no-store"
    exam = _get_exam_by_share_token(db, token)
    return build_exam_statistics(exam, bonus_points_override=bonus_points_override)
