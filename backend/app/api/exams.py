"""Exam CRUD (§4, §7, §8.1, §13 — ``docs/api-contract.md`` section "Exams").

This module also owns the lecture/exam **access helpers** and the exam **serialisation**
helpers, because ``app/api/lectures.py`` needs both and this keeps the import edge
one-directional (``lectures`` → ``exams``, never back).

Authorisation anchors on ``Exam.owner_id`` and never on the parent lecture's owner: §4 makes an
exam's owner *default* to the lecture's owner but stay independently editable, so authorising an
exam through its lecture would let a reassignment silently break access control. Cross-owner
access answers ``404``, not ``403`` — a ``403`` would confirm the row exists, which leaks the
existence of another instructor's exam data.
"""

from __future__ import annotations

from decimal import Decimal

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import Select, func, or_, select
from sqlalchemy.orm import Session

from app.api.schemas import (
    ExamCreateRequest,
    ExamDetail,
    ExamSummary,
    ExamUpdateRequest,
    ExerciseInput,
    ExerciseOut,
    GradeThresholdInput,
    GradeThresholdOut,
    RecomputationWarning,
)
from app.auth.dependencies import CurrentUser, DbSession
from app.grading.schema import GRADES, threshold_points, validate_grading_schema
from app.models import (
    BonusMode,
    Exam,
    Exercise,
    ExercisePoints,
    GradeThreshold,
    Lecture,
    StudentRegistration,
    User,
)

router = APIRouter(tags=["exams"])

LECTURE_NOT_FOUND_DETAIL = "Vorlesung nicht gefunden."
EXAM_NOT_FOUND_DETAIL = "Prüfung nicht gefunden."
EXAM_DELETE_CONFIRM_DETAIL = (
    "Das Löschen der Prüfung entfernt unwiderruflich alle Anmeldungen, Punkte und Noten "
    "dieser Prüfung. Bitte mit ?confirm=true bestätigen."
)
UNKNOWN_OWNER_DETAIL = "Der angegebene Besitzer existiert nicht oder ist deaktiviert."
OWNER_REQUIRED_DETAIL = "Die Prüfung muss einen Besitzer haben."
SEMESTER_REQUIRED_DETAIL = "Das Semester darf nicht leer sein."
TERMIN_REQUIRED_DETAIL = "Der Termin darf nicht leer sein."


def _raise_validation_errors(errors: list[str]) -> None:
    """Raise the contract's ``422`` shape (``{"detail": {"errors": [...]}}``) if non-empty.

    Literal ``422`` because starlette has deprecated the ``HTTP_422_UNPROCESSABLE_ENTITY``
    constant in favour of a renamed one (same reasoning as ``app/api/admin.py``).
    """
    if errors:
        raise HTTPException(status_code=422, detail={"errors": errors})


def _format_decimal(value: Decimal) -> str:
    """German number formatting for a user-facing message (comma decimal separator)."""
    return f"{value:f}".replace(".", ",")


# --------------------------------------------------------------------------------------------
# Access helpers — the 404-not-403 rule lives here
# --------------------------------------------------------------------------------------------


def get_owned_lecture(db: Session, user: User, lecture_id: int) -> Lecture:
    """The caller's lecture, or ``404`` — identical response for "missing" and "someone else's"."""
    lecture = db.get(Lecture, lecture_id)
    if lecture is None or lecture.owner_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=LECTURE_NOT_FOUND_DETAIL)
    return lecture


def get_owned_exam(db: Session, user: User, exam_id: int) -> Exam:
    """The caller's exam, or ``404``.

    Ownership is ``Exam.owner_id`` alone. An exam that was reassigned to another instructor is
    invisible to the lecture's owner from that moment on, and visible to its new owner even
    though the lecture above it still belongs to someone else — that is the point of §4's
    independently editable exam owner.
    """
    exam = db.get(Exam, exam_id)
    if exam is None or exam.owner_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=EXAM_NOT_FOUND_DETAIL)
    return exam


# --------------------------------------------------------------------------------------------
# Serialisation
# --------------------------------------------------------------------------------------------


def total_max_points(exam: Exam) -> Decimal:
    """Sum of the exam's exercises' ``max_points``.

    Summed in Python over decoded ``Decimal`` values, never with SQL ``SUM()``: the column is
    ``TEXT`` (see ``app/types.py``), so an SQL aggregate would go through SQLite's numeric
    coercion — a binary float.
    """
    return sum((exercise.max_points for exercise in exam.exercises), Decimal(0))


def exam_summary(exam: Exam) -> ExamSummary:
    """The contract's exam-summary shape."""
    return ExamSummary(
        id=exam.id,
        lecture_id=exam.lecture_id,
        lecture_name=exam.lecture.name,
        semester=exam.semester,
        termin=exam.termin,
        exam_date=exam.exam_date,
        bonus_mode=exam.bonus_mode,
        owner_id=exam.owner_id,
    )


def _grading_schema_out(exam: Exam) -> list[GradeThresholdOut]:
    """The exam's schema in §7.1 grade order, each with its server-computed point threshold."""
    maximum = total_max_points(exam)
    by_grade = {threshold.grade: threshold.percentage for threshold in exam.grade_thresholds}
    return [
        GradeThresholdOut(
            grade=grade,
            percentage=by_grade[grade],
            threshold_points=threshold_points(by_grade[grade], maximum),
        )
        for grade in GRADES
        if grade in by_grade
    ]


def exam_detail(db: Session, exam: Exam, warning: RecomputationWarning | None = None) -> ExamDetail:
    """The contract's exam-detail shape, with ``threshold_points`` computed server-side."""
    return ExamDetail(
        **exam_summary(exam).model_dump(),
        exercises=[ExerciseOut.model_validate(exercise) for exercise in exam.exercises],
        grading_schema=_grading_schema_out(exam),
        registration_count=count_registrations(db, exam),
        total_max_points=total_max_points(exam),
        recomputation_warning=warning,
    )


# --------------------------------------------------------------------------------------------
# §8.1 recomputation plumbing
# --------------------------------------------------------------------------------------------


def count_registrations(db: Session, exam: Exam) -> int:
    """All of the exam's registrations, excluded ones included (§5.3: excluded ≠ deleted)."""
    return int(
        db.execute(
            select(func.count())
            .select_from(StudentRegistration)
            .where(StudentRegistration.exam_id == exam.id)
        ).scalar_one()
    )


def count_affected_registrations(db: Session, exam: Exam) -> int:
    """Registrations that carry data an exercise/schema edit could move the grade of (§8.1).

    A registration counts as affected once it carries data a grade is derived from: at least one
    recorded ``ExercisePoints`` row, or a recorded ``attended`` flag. Excluded students are left
    out — they appear in no grade, list or report (§5.3).

    This is a coarser number than :func:`~app.api.points.grade_snapshot`'s
    ``grades_changed`` (see ``update_exam``, below): it counts everyone an edit *could* touch,
    not everyone whose grade *string* actually differs afterwards. ``final_total``/``grade`` are
    never stored columns — every response computes them fresh from ``ExercisePoints`` and the
    exam's current exercises/schema (app/models/registration.py), so "recomputing" a grade is
    just reading it again after the edit; there is nothing here to persist.
    """
    return int(
        db.execute(
            select(func.count(func.distinct(StudentRegistration.id)))
            .select_from(StudentRegistration)
            .outerjoin(ExercisePoints, ExercisePoints.registration_id == StudentRegistration.id)
            .where(
                StudentRegistration.exam_id == exam.id,
                StudentRegistration.excluded.is_(False),
                or_(
                    ExercisePoints.id.is_not(None),
                    StudentRegistration.attended.is_not(None),
                ),
            )
        ).scalar_one()
    )


# --------------------------------------------------------------------------------------------
# Validation and collection replacement
# --------------------------------------------------------------------------------------------


def _validate_exercises(items: list[ExerciseInput]) -> list[str]:
    """German error messages for an exercise list (empty list = valid: an exam may have none)."""
    errors: list[str] = []
    for index, item in enumerate(items, start=1):
        label = item.name.strip() or f"Aufgabe {index}"
        if not item.name.strip():
            errors.append(f"Aufgabe {index}: Der Name darf nicht leer sein.")
        if item.max_points <= 0:
            errors.append(
                f"Aufgabe {index} ({label}): Die maximale Punktzahl muss größer als 0 sein "
                f"(aktuell: {_format_decimal(item.max_points)})."
            )
    return errors


def _validate_grading_schema_input(
    items: list[GradeThresholdInput],
) -> tuple[dict[str, Decimal], list[str]]:
    """Validate a submitted schema; return ``(percentages, German errors)``.

    An **empty** schema is allowed (a freshly created exam has none yet); a non-empty one must be
    complete and strictly decreasing per §7.2. Percentages reach
    :func:`~app.grading.schema.validate_grading_schema` as ``Decimal`` because they were parsed
    from their wire *string* at the request boundary — that function raises ``TypeError`` on a
    float by design (§7.0), and a ``TypeError`` here would surface as a ``500``.
    """
    if not items:
        return {}, []

    errors: list[str] = []
    percentages: dict[str, Decimal] = {}
    duplicates: set[str] = set()
    for item in items:
        if item.grade in percentages:
            duplicates.add(item.grade)
        percentages[item.grade] = item.percentage
    if duplicates:
        errors.append("Doppelte Noten im Notenschema: " + ", ".join(sorted(duplicates)) + ".")

    errors.extend(validate_grading_schema(percentages))
    return percentages, errors


def _replace_exercises(db: Session, exam: Exam, items: list[ExerciseInput]) -> None:
    """Full replace of the exam's exercises, renumbered ``1..N`` in submitted order.

    Positions are assigned server-side rather than taken from the payload so they are always
    unique and contiguous. The ``flush()`` between the deletes and the inserts matters: without
    it a reorder of the same positions would hit ``uq_exercise_exam_position`` mid-statement.
    """
    for exercise in list(exam.exercises):
        exam.exercises.remove(exercise)
    db.flush()
    for position, item in enumerate(items, start=1):
        exam.exercises.append(
            Exercise(name=item.name.strip(), max_points=item.max_points, position=position)
        )
    db.flush()


def _replace_grading_schema(db: Session, exam: Exam, percentages: dict[str, Decimal]) -> None:
    """Full replace of the exam's grade thresholds (same flush-between reasoning as above)."""
    for threshold in list(exam.grade_thresholds):
        exam.grade_thresholds.remove(threshold)
    db.flush()
    for grade in GRADES:
        if grade in percentages:
            exam.grade_thresholds.append(GradeThreshold(grade=grade, percentage=percentages[grade]))
    db.flush()


def _resolve_owner(db: Session, owner_id: int) -> User:
    """An existing, **active** user, or a ``422`` with a German message (contract: Exams)."""
    owner = db.get(User, owner_id)
    if owner is None or not owner.is_active:
        _raise_validation_errors([UNKNOWN_OWNER_DETAIL])
        raise AssertionError("unreachable")  # pragma: no cover
    return owner


# --------------------------------------------------------------------------------------------
# Copy-forward (§4)
# --------------------------------------------------------------------------------------------


def most_recent_prior_exam(db: Session, lecture: Lecture) -> Exam | None:
    """The exam whose settings a new exam under ``lecture`` copies forward (§4).

    §4 only says "most recent prior Exam", which is ambiguous as soon as two sittings share a
    date or a date is missing. The rule settled on here, and the one the tests pin:

    1. ``exam_date`` **descending**;
    2. a ``NULL`` ``exam_date`` counts as **oldest** (an undated draft never wins over a dated
       sitting), so nulls sort last;
    3. ties broken by ``id`` **descending** — the most recently created row wins.

    Ordering is done in SQL on ``Date``/``Integer`` columns only. No decimal column is ever
    ordered in SQL (``app/types.py``: they are ``TEXT`` and would sort lexicographically).
    """
    statement = (
        select(Exam)
        .where(Exam.lecture_id == lecture.id)
        .order_by(Exam.exam_date.is_(None).asc(), Exam.exam_date.desc(), Exam.id.desc())
        .limit(1)
    )
    return db.execute(statement).scalars().first()


# --------------------------------------------------------------------------------------------
# Routes
# --------------------------------------------------------------------------------------------


@router.get("/exams", response_model=list[ExamSummary])
def list_exams(
    user: CurrentUser,
    db: DbSession,
    lecture_id: int | None = Query(default=None),
) -> list[ExamSummary]:
    """The caller's exams, newest sitting first, optionally narrowed to one lecture.

    ``lecture_id`` is a plain filter and deliberately **not** an ownership check on the lecture:
    an exam reassigned to this caller (§4) may still hang under a lecture owned by someone else,
    and it must stay listable by its owner. Filtering on ``Exam.owner_id`` is what keeps other
    instructors' exams out, and an unknown/foreign ``lecture_id`` simply yields ``[]``.
    """
    statement: Select[tuple[Exam]] = (
        select(Exam)
        .where(Exam.owner_id == user.id)
        .order_by(Exam.exam_date.is_(None).asc(), Exam.exam_date.desc(), Exam.id.desc())
    )
    if lecture_id is not None:
        statement = statement.where(Exam.lecture_id == lecture_id)
    return [exam_summary(exam) for exam in db.execute(statement).scalars().all()]


@router.post(
    "/lectures/{lecture_id}/exams",
    response_model=ExamDetail,
    status_code=status.HTTP_201_CREATED,
)
def create_exam(
    lecture_id: int, payload: ExamCreateRequest, user: CurrentUser, db: DbSession
) -> ExamDetail:
    """Create an exam under one of the caller's lectures.

    ``bonus_mode``, ``exercises`` and ``grading_schema`` are copied forward from
    :func:`most_recent_prior_exam` when the field is **absent** from the request body (§4) — a
    one-time copy at creation time; nothing stays linked afterwards. An explicitly sent value,
    including an empty list, wins over the copy: "no exercises yet" must be expressible.

    The new exam's owner is the lecture's owner (i.e. the caller) and is editable afterwards via
    ``PATCH`` (§4).
    """
    lecture = get_owned_lecture(db, user, lecture_id)
    sent = payload.model_fields_set

    if not payload.semester.strip():
        _raise_validation_errors([SEMESTER_REQUIRED_DETAIL])
    if not payload.termin.strip():
        _raise_validation_errors([TERMIN_REQUIRED_DETAIL])

    source = (
        None
        if {"bonus_mode", "exercises", "grading_schema"} <= sent
        else most_recent_prior_exam(db, lecture)
    )

    if "exercises" in sent:
        exercises = [
            ExerciseInput(name=item.name, max_points=item.max_points)
            for item in (payload.exercises or [])
        ]
    elif source is not None:
        exercises = [
            ExerciseInput(name=item.name, max_points=item.max_points) for item in source.exercises
        ]
    else:
        exercises = []

    if "grading_schema" in sent:
        schema_input = payload.grading_schema or []
    elif source is not None:
        schema_input = [
            GradeThresholdInput(grade=item.grade, percentage=item.percentage)
            for item in source.grade_thresholds
        ]
    else:
        schema_input = []

    errors = _validate_exercises(exercises)
    percentages, schema_errors = _validate_grading_schema_input(schema_input)
    _raise_validation_errors(errors + schema_errors)

    if payload.bonus_mode is not None:
        bonus_mode = payload.bonus_mode
    elif source is not None:
        bonus_mode = source.bonus_mode
    else:
        bonus_mode = BonusMode.ALWAYS

    exam = Exam(
        lecture_id=lecture.id,
        owner_id=lecture.owner_id,
        semester=payload.semester.strip(),
        termin=payload.termin.strip(),
        exam_date=payload.exam_date,
        bonus_mode=bonus_mode,
    )
    db.add(exam)
    db.flush()
    _replace_exercises(db, exam, exercises)
    _replace_grading_schema(db, exam, percentages)
    db.commit()
    db.refresh(exam)
    return exam_detail(db, exam)


@router.get("/exams/{exam_id}", response_model=ExamDetail)
def read_exam(exam_id: int, user: CurrentUser, db: DbSession) -> ExamDetail:
    """One exam with its exercises, grading schema and computed thresholds."""
    return exam_detail(db, get_owned_exam(db, user, exam_id))


@router.patch("/exams/{exam_id}", response_model=ExamDetail)
def update_exam(
    exam_id: int, payload: ExamUpdateRequest, user: CurrentUser, db: DbSession
) -> ExamDetail:
    """Update an exam. ``exercises``/``grading_schema`` are a **full replace**, never a merge.

    When such a replace happens while the exam already has registrations, the response carries a
    ``recomputation_warning`` — §8.1 forbids grade thresholds shifting silently under data an
    instructor may already have transcribed onto paper exams. ``grades_changed`` on that warning
    is a snapshot-diff: every non-excluded registration's computed grade string is taken *before*
    the mutation and again *after* it (via ``app.api.points.grade_snapshot``, function-local
    import — see the comment below on why), and the two are compared per registration. This is
    deliberately stricter than ``affected_registrations`` (:func:`count_affected_registrations`):
    a schema edit that leaves every 0.5-point threshold exactly where it was must report ``0``
    changed grades even though registrations carry data, so the warning does not fire on every
    edit regardless of effect.

    One sharp edge, not exercised by any test because the contract never asks for it:
    ``bonus_mode`` is applied above, before the "before" snapshot is taken. A request that changes
    ``bonus_mode`` *and* ``grading_schema``/``exercises`` in the same ``PATCH`` therefore snapshots
    "before" under the *new* bonus_mode already, so ``grades_changed`` only reflects the
    threshold/exercise move, not the bonus_mode change layered on top of it. This matches the
    contract, which scopes the warning to an ``exercises``/``grading_schema`` replace — a
    bonus_mode-only edit never triggers it at all — but is worth knowing if this ever needs
    tightening.
    """
    exam = get_owned_exam(db, user, exam_id)
    sent = payload.model_fields_set

    errors: list[str] = []
    if "semester" in sent and not (payload.semester or "").strip():
        errors.append(SEMESTER_REQUIRED_DETAIL)
    if "termin" in sent and not (payload.termin or "").strip():
        errors.append(TERMIN_REQUIRED_DETAIL)
    if "owner_id" in sent and payload.owner_id is None:
        errors.append(OWNER_REQUIRED_DETAIL)

    exercises = list(payload.exercises or []) if "exercises" in sent else None
    if exercises is not None:
        errors.extend(_validate_exercises(exercises))

    percentages: dict[str, Decimal] | None = None
    if "grading_schema" in sent:
        percentages, schema_errors = _validate_grading_schema_input(payload.grading_schema or [])
        errors.extend(schema_errors)

    # Everything is validated before anything is mutated, so a rejected request leaves the exam
    # exactly as it was.
    _raise_validation_errors(errors)

    if payload.owner_id is not None:
        exam.owner_id = _resolve_owner(db, payload.owner_id).id
    if payload.semester is not None:
        exam.semester = payload.semester.strip()
    if payload.termin is not None:
        exam.termin = payload.termin.strip()
    if "exam_date" in sent:
        exam.exam_date = payload.exam_date
    if payload.bonus_mode is not None:
        exam.bonus_mode = payload.bonus_mode

    thresholds_moved = exercises is not None or percentages is not None
    has_registrations = count_registrations(db, exam) > 0

    # §8.1: snapshot every non-excluded registration's *computed* grade string before the
    # mutation, so the warning below can report how many actually changed rather than merely how
    # many carry data (that is what affected_registrations, below, already answers). Function-
    # local import: app/api/points.py imports get_owned_exam/total_max_points from this module at
    # module scope, so importing it back here at module scope would cycle.
    before_grades: dict[int, str | None] = {}
    if thresholds_moved and has_registrations:
        from app.api.points import grade_snapshot

        before_grades = grade_snapshot(exam)

    if exercises is not None:
        _replace_exercises(db, exam, exercises)
    if percentages is not None:
        _replace_grading_schema(db, exam, percentages)

    warning: RecomputationWarning | None = None
    if thresholds_moved and has_registrations:
        from app.api.points import grade_snapshot

        # A full exercise replace deletes the old Exercise rows; SQLite's ON DELETE CASCADE
        # (app/db.py) removes their ExercisePoints at the database level, invisibly to the ORM
        # session (Exercise carries no ORM-level cascade to ExercisePoints — see
        # app/models/registration.py). Expire everything so the "after" snapshot below re-reads
        # from the database rather than a stale in-session ExercisePoints collection.
        db.expire_all()
        after_grades = grade_snapshot(exam)
        grades_changed = sum(
            1
            for registration_id, before_grade in before_grades.items()
            if after_grades.get(registration_id) != before_grade
        )
        warning = RecomputationWarning(
            changed=True,
            affected_registrations=count_affected_registrations(db, exam),
            grades_changed=grades_changed,
        )

    db.commit()
    # expire_on_commit is off (app/db.py), so the replaced collections would otherwise still be
    # the pre-commit ones — refresh explicitly rather than serialising a stale exam.
    db.refresh(exam)
    return exam_detail(db, exam, warning)


@router.delete("/exams/{exam_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_exam(
    exam_id: int,
    user: CurrentUser,
    db: DbSession,
    confirm: bool = Query(default=False),
) -> None:
    """Delete an exam and everything hanging off it (§13).

    Cascades to exercises, grade thresholds, registrations and their points — at the database
    level, which is what §13's "delete an exam and all its personal data" requires. Needs
    ``?confirm=true``: this destroys grades and there is no undo.
    """
    exam = get_owned_exam(db, user, exam_id)
    if not confirm:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=EXAM_DELETE_CONFIRM_DETAIL)
    db.delete(exam)
    db.commit()
