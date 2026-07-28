"""Lecture CRUD (§4, §13 — ``docs/api-contract.md`` section "Lectures").

A lecture is a purely internal organisational label chosen by the instructor (§4) — never
derived from or validated against a registration PDF's title line. Its only job is to group an
exam's recurring sittings so settings can be copied forward (see ``app/api/exams.py``).

Everything here is scoped to ``Lecture.owner_id``, and a lecture owned by someone else answers
``404`` rather than ``403`` (see ``app/api/exams.py`` for the reasoning). Access and exam
serialisation helpers are imported from that module to keep the import edge one-directional.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.exams import exam_summary, get_owned_lecture
from app.api.schemas import (
    LectureCreateRequest,
    LectureDetail,
    LectureSummary,
    LectureUpdateRequest,
)
from app.auth.dependencies import CurrentUser, DbSession
from app.models import Exam, Lecture

router = APIRouter(prefix="/lectures", tags=["lectures"])

LECTURE_DELETE_CONFIRM_DETAIL = (
    "Das Löschen der Vorlesung entfernt unwiderruflich alle zugehörigen Prüfungen samt "
    "Anmeldungen, Punkten und Noten. Bitte mit ?confirm=true bestätigen."
)


def _exam_count(db: Session, lecture: Lecture) -> int:
    """Number of exams under ``lecture``, regardless of who owns them.

    Counted without an owner filter on purpose: this is a property of the lecture, and an exam
    reassigned to a colleague (§4) is still one of this lecture's sittings. It is a count, not a
    disclosure of the exam's contents.
    """
    return int(
        db.execute(
            select(func.count()).select_from(Exam).where(Exam.lecture_id == lecture.id)
        ).scalar_one()
    )


def _summary(db: Session, lecture: Lecture) -> LectureSummary:
    return LectureSummary(
        id=lecture.id,
        name=lecture.name,
        created_at=lecture.created_at,
        exam_count=_exam_count(db, lecture),
    )


@router.get("", response_model=list[LectureSummary])
def list_lectures(user: CurrentUser, db: DbSession) -> list[LectureSummary]:
    """The caller's lectures, by name."""
    lectures = (
        db.execute(select(Lecture).where(Lecture.owner_id == user.id).order_by(Lecture.name))
        .scalars()
        .all()
    )
    return [_summary(db, lecture) for lecture in lectures]


@router.post("", response_model=LectureSummary, status_code=status.HTTP_201_CREATED)
def create_lecture(
    payload: LectureCreateRequest, user: CurrentUser, db: DbSession
) -> LectureSummary:
    """Create a lecture owned by the caller.

    Duplicate names are deliberately allowed: the name is a free-text internal label (§4), and
    two lectures may legitimately share one across different degree programmes.
    """
    lecture = Lecture(name=payload.name.strip(), owner_id=user.id)
    db.add(lecture)
    db.commit()
    db.refresh(lecture)
    return _summary(db, lecture)


@router.get("/{lecture_id}", response_model=LectureDetail)
def read_lecture(lecture_id: int, user: CurrentUser, db: DbSession) -> LectureDetail:
    """One lecture with its exams, most recent sitting first."""
    lecture = get_owned_lecture(db, user, lecture_id)
    exams = (
        db.execute(
            select(Exam)
            .where(Exam.lecture_id == lecture.id)
            .order_by(Exam.exam_date.is_(None).asc(), Exam.exam_date.desc(), Exam.id.desc())
        )
        .scalars()
        .all()
    )
    return LectureDetail(
        **_summary(db, lecture).model_dump(),
        exams=[exam_summary(exam) for exam in exams],
    )


@router.patch("/{lecture_id}", response_model=LectureSummary)
def update_lecture(
    lecture_id: int, payload: LectureUpdateRequest, user: CurrentUser, db: DbSession
) -> LectureSummary:
    """Rename a lecture. Renaming never touches its exams' data (§4)."""
    lecture = get_owned_lecture(db, user, lecture_id)
    lecture.name = payload.name.strip()
    db.commit()
    db.refresh(lecture)
    return _summary(db, lecture)


@router.delete("/{lecture_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_lecture(
    lecture_id: int,
    user: CurrentUser,
    db: DbSession,
    confirm: bool = Query(default=False),
) -> None:
    """Delete a lecture and, by cascade, every exam under it and all their data (§13).

    Requires ``?confirm=true``: this is the most destructive action in the app, and it reaches
    further than the caller may expect — including exams they no longer own themselves.
    """
    lecture = get_owned_lecture(db, user, lecture_id)
    if not confirm:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=LECTURE_DELETE_CONFIRM_DETAIL
        )
    db.delete(lecture)
    db.commit()
