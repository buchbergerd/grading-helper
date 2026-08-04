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

import json
import unicodedata
from decimal import Decimal
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, File, HTTPException, Query, Response, UploadFile, status
from pydantic import ValidationError
from sqlalchemy import Select, func, or_, select
from sqlalchemy.orm import Session

from app.api.schemas import (
    ExamCreateRequest,
    ExamDetail,
    ExamExportExercise,
    ExamExportGradeThreshold,
    ExamExportPayload,
    ExamExportRegistration,
    ExamImportResult,
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
BONUS_POINTS_NEGATIVE_DETAIL = "Die Bonuspunkte dürfen nicht negativ sein."
LECTURE_NAME_REQUIRED_DETAIL = "Der Name der Vorlesung darf nicht leer sein."
IMPORT_INVALID_JSON_DETAIL = "Die Datei ist keine gültige JSON-Datei."
IMPORT_FORMAT_VERSION_DETAIL = "Diese Datei stammt aus einer nicht unterstützten Programmversion."
EXPORT_MEDIA_TYPE = "application/json"


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
        bonus_points=exam.bonus_points,
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
        share_token=exam.share_token,
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
    """German error messages for an exercise list (empty list = valid: an exam may have none).

    A repeated ``id`` would make ``_replace_exercises`` write the same row twice and leave a
    position gap in the result — reject it here rather than let it corrupt the exam's position
    numbering.
    """
    errors: list[str] = []
    seen_ids: set[int] = set()
    for index, item in enumerate(items, start=1):
        label = item.name.strip() or f"Aufgabe {index}"
        if not item.name.strip():
            errors.append(f"Aufgabe {index}: Der Name darf nicht leer sein.")
        if item.max_points <= 0:
            errors.append(
                f"Aufgabe {index} ({label}): Die maximale Punktzahl muss größer als 0 sein "
                f"(aktuell: {_format_decimal(item.max_points)})."
            )
        if item.id is not None:
            if item.id in seen_ids:
                errors.append(f"Aufgabe {index} ({label}): Doppelte Aufgaben-ID.")
            seen_ids.add(item.id)
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
    """Diff-replace the exam's exercises, matched to existing rows by ``id``, renumbered
    ``1..N`` in submitted order.

    An item whose ``id`` matches one of the exam's current exercises updates that row in place
    rather than deleting and recreating it. That matters because ``ExercisePoints`` only
    cascades from ``Exercise`` at the database level, invisibly to the ORM (no ORM-level
    delete-orphan on that side — see ``app/models/registration.py``): deleting and recreating an
    unchanged exercise's row — e.g. because the instructor only added *another* exercise, or
    reordered — would silently wipe out every already-entered point for it. An item with no
    matching ``id`` (new, or naming an exercise outside this exam) becomes a new row; an existing
    exercise absent from the submission is removed, taking its ``ExercisePoints`` with it — the
    one case where losing its points is correct, since the exercise itself no longer exists.

    Positions are assigned server-side rather than taken from the payload so they are always
    unique and contiguous. Kept rows pass through a scratch negative position first: setting a
    reordered row directly to its final position can collide with another kept row still sitting
    there, which would hit ``uq_exercise_exam_position`` mid-statement (SQLite constraints are
    checked immediately, not deferred) — ``-id`` is unique and never collides with a real
    (positive) position.
    """
    existing_by_id = {exercise.id: exercise for exercise in exam.exercises}
    matches = [existing_by_id.get(item.id) if item.id is not None else None for item in items]
    kept_ids = {exercise.id for exercise in matches if exercise is not None}

    for existing_exercise in list(exam.exercises):
        if existing_exercise.id not in kept_ids:
            exam.exercises.remove(existing_exercise)
    db.flush()

    for exercise in matches:
        if exercise is not None:
            exercise.position = -exercise.id
    db.flush()

    for position, (item, exercise) in enumerate(zip(items, matches, strict=True), start=1):
        name = item.name.strip()
        if exercise is not None:
            exercise.name = name
            exercise.max_points = item.max_points
            exercise.position = position
        else:
            exam.exercises.append(
                Exercise(name=name, max_points=item.max_points, position=position)
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
    ``bonus_points`` is **not** copied forward — it is this exam's own entered result, not
    reusable configuration (§4), so an absent value always starts at 0 regardless of any prior
    exam.

    The new exam's owner is the lecture's owner (i.e. the caller) and is editable afterwards via
    ``PATCH`` (§4).
    """
    lecture = get_owned_lecture(db, user, lecture_id)
    sent = payload.model_fields_set

    if not payload.semester.strip():
        _raise_validation_errors([SEMESTER_REQUIRED_DETAIL])
    if not payload.termin.strip():
        _raise_validation_errors([TERMIN_REQUIRED_DETAIL])
    if payload.bonus_points is not None and payload.bonus_points < 0:
        _raise_validation_errors([BONUS_POINTS_NEGATIVE_DETAIL])

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
        bonus_points=payload.bonus_points if payload.bonus_points is not None else Decimal(0),
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

    When such a replace happens (or ``bonus_points`` actually changes) while the exam already has
    registrations, the response carries a ``recomputation_warning`` — §8.1 forbids grade
    thresholds shifting silently under data an instructor may already have transcribed onto paper
    exams. Since ``bonus_points`` (§7.3) is now one amount for the whole exam rather than a
    per-student field, editing it can move every non-excluded student's grade in a single edit —
    exactly the silent-shift risk this gate exists to catch. ``grades_changed`` on that warning is
    a snapshot-diff: every non-excluded registration's computed grade string is taken *before* the
    mutation and again *after* it (via ``app.api.points.grade_snapshot``, function-local import —
    see the comment below on why), and the two are compared per registration. This is deliberately
    stricter than ``affected_registrations`` (:func:`count_affected_registrations`): a schema (or
    bonus_points) edit that leaves every grade exactly where it was must report ``0`` changed
    grades even though registrations carry data, so the warning does not fire on every edit
    regardless of effect.

    One sharp edge, not exercised by any test because the contract never asks for it:
    ``bonus_mode`` is applied above, before the "before" snapshot is taken (unlike
    ``bonus_points``, which is applied *after* it — see below). A request that changes
    ``bonus_mode`` *and* ``grading_schema``/``exercises``/``bonus_points`` in the same ``PATCH``
    therefore snapshots "before" under the *new* bonus_mode already, so ``grades_changed`` only
    reflects the other moves, not the bonus_mode change layered on top of it. This matches the
    contract, which scopes the warning to exercises/grading_schema/bonus_points — a
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
    if payload.bonus_points is not None and payload.bonus_points < 0:
        errors.append(BONUS_POINTS_NEGATIVE_DETAIL)

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

    # Compared against the *old* value before bonus_points is actually written below, so the
    # before/after grade snapshot brackets this change like an exercises/grading_schema replace
    # rather than missing it the way the bonus_mode sharp edge (above) does.
    bonus_points_changed = (
        payload.bonus_points is not None and payload.bonus_points != exam.bonus_points
    )
    thresholds_moved = exercises is not None or percentages is not None or bonus_points_changed
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
    if payload.bonus_points is not None:
        exam.bonus_points = payload.bonus_points
        # The session has autoflush disabled (app/db.py), and the "after" snapshot below calls
        # db.expire_all() — which, with nothing flushed yet, would discard this assignment and
        # make the "after" grade snapshot silently re-read the *old* bonus_points from the
        # database. Flushing here is what makes it stick before that expire.
        db.flush()

    warning: RecomputationWarning | None = None
    if thresholds_moved and has_registrations:
        from app.api.points import grade_snapshot

        # An exercise replace may delete Exercise rows the submission dropped; SQLite's ON DELETE
        # CASCADE (app/db.py) removes their ExercisePoints at the database level, invisibly to the
        # ORM session (Exercise carries no ORM-level cascade to ExercisePoints — see
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


# --------------------------------------------------------------------------------------------
# Whole-exam export/import — backup / transfer between instructors or installations. Not part of
# any §15 milestone; added afterwards by request. Deliberately lives here rather than in a
# separate module: it needs exactly the validation/replace helpers and models this module already
# owns, and splitting it out would either duplicate them or force those helpers to become a wider
# public surface for one extra caller (see ``_validate_exercises``/``_replace_exercises`` etc.,
# all still module-private).
# --------------------------------------------------------------------------------------------

_FILENAME_ASCII_TRANSLITERATION = {
    "ä": "ae",
    "ö": "oe",
    "ü": "ue",
    "Ä": "Ae",
    "Ö": "Oe",
    "Ü": "Ue",
    "ß": "ss",
}


def _sanitize_filename_part(value: str) -> str:
    """One filename component: no path separators, no whitespace, no empty result.

    A small local duplicate of ``app/reports/attendance_list.py``'s helper of the same purpose,
    kept separate on purpose: importing that module here would pull its module-level ``import
    typst`` into exam CRUD's import graph — which every other API module in turn imports from —
    just to reuse a header-string helper.
    """
    cleaned = "".join("-" if char in "/\\:" else char for char in value)
    cleaned = "_".join(cleaned.split())
    cleaned = cleaned.strip("._-")
    return cleaned or "unbenannt"


def _export_filename(exam: Exam) -> str:
    """E.g. ``Export_WiSe_23-24_1._Termin.json``."""
    parts = ["Export", _sanitize_filename_part(exam.semester), _sanitize_filename_part(exam.termin)]
    return "_".join(parts) + ".json"


def _export_content_disposition(filename: str) -> str:
    """Same ASCII-fallback-plus-RFC-5987 ``Content-Disposition`` shape as every report download."""
    stem = filename.removesuffix(".json")
    expanded = "".join(_FILENAME_ASCII_TRANSLITERATION.get(char, char) for char in stem)
    ascii_stem = "".join(
        char
        for char in unicodedata.normalize("NFKD", expanded)
        if char.isascii() and char.isprintable()
    )
    ascii_name = _sanitize_filename_part(ascii_stem) + ".json"
    quoted = quote(filename, safe="")
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quoted}"


@router.get(
    "/exams/{exam_id}/export",
    response_class=Response,
    responses={
        200: {
            "content": {EXPORT_MEDIA_TYPE: {}},
            "description": (
                "Die Klausur (Eckdaten, Aufgaben, Notenschlüssel, Anmeldungen und Punkte) als "
                "JSON-Datei."
            ),
        }
    },
)
def export_exam(exam_id: int, user: CurrentUser, db: DbSession) -> Response:
    """Export one exam as a single downloadable JSON file (backup, or transfer to another
    instructor/installation via :func:`import_exam`).

    Bundles everything a re-import needs: settings, exercises, the grading schema, and every
    registration with its points — **including excluded registrations** (§5.3: excluded is an
    audit flag, never a deletion, and dropping them here would make the round trip lossy).
    ``owner_id`` is deliberately not part of the payload — see ``ExamExportPayload``'s docstring.

    Each registration's ``points`` is keyed by the 1-based index of the exercise within this
    file's own ``exercises`` list (see ``ExamExportRegistration``), not by ``Exercise.position``
    or any database id — both would be meaningless, or actively misleading, once re-imported as
    new rows.
    """
    exam = get_owned_exam(db, user, exam_id)
    exercises = list(exam.exercises)  # relationship is ordered by position (app/models/exam.py)
    index_by_exercise_id = {exercise.id: index for index, exercise in enumerate(exercises, start=1)}
    by_grade = {threshold.grade: threshold.percentage for threshold in exam.grade_thresholds}

    payload = ExamExportPayload(
        lecture_name=exam.lecture.name,
        semester=exam.semester,
        termin=exam.termin,
        exam_date=exam.exam_date,
        bonus_mode=exam.bonus_mode,
        bonus_points=exam.bonus_points,
        exercises=[
            ExamExportExercise(
                name=exercise.name, max_points=exercise.max_points, position=exercise.position
            )
            for exercise in exercises
        ],
        grading_schema=[
            ExamExportGradeThreshold(grade=grade, percentage=by_grade[grade])
            for grade in GRADES
            if grade in by_grade
        ],
        registrations=[
            ExamExportRegistration(
                matrikelnummer=registration.matrikelnummer,
                nachname=registration.nachname,
                vorname=registration.vorname,
                course_code=registration.course_code,
                module_title=registration.module_title,
                versuch=registration.versuch,
                kommentar=registration.kommentar,
                flagged=registration.flagged,
                excluded=registration.excluded,
                attended=registration.attended,
                source_filename=registration.source_filename,
                points={
                    str(index_by_exercise_id[points_row.exercise_id]): points_row.points
                    for points_row in registration.exercise_points
                },
            )
            for registration in exam.registrations
        ],
    )
    body = payload.model_dump_json(indent=2).encode("utf-8")
    return Response(
        content=body,
        media_type=EXPORT_MEDIA_TYPE,
        headers={
            "Content-Disposition": _export_content_disposition(_export_filename(exam)),
            # Names and Matrikelnummern (§13 treats these as real personal data) — keep this out
            # of shared/proxy caches and browser back-button caches, same as every other download
            # of exam data (see app/reports/attendance_list.py::attendance_list_report).
            "Cache-Control": "no-store",
        },
    )


def _format_import_structure_errors(exc: ValidationError) -> list[str]:
    """Best-effort messages for a structurally invalid export file.

    Mirrors the contract's own carve-out for standard FastAPI/Pydantic validation errors keeping
    their default shape (docs/api-contract.md, Exams section) — applied here to a file upload
    instead of a JSON request body: a corrupted or foreign file is reported with its field path
    and pydantic's own message, rather than a hand-translated German sentence for every one of
    the many ways a file could fail to match :class:`~app.api.schemas.ExamExportPayload`.
    """
    messages: list[str] = []
    for error in exc.errors():
        location = ".".join(str(part) for part in error["loc"])
        messages.append(f"Exportdatei, Feld „{location}“: {error['msg']}")
    return messages


@router.post(
    "/exams/import", response_model=ExamImportResult, status_code=status.HTTP_201_CREATED
)
def import_exam(
    user: CurrentUser, db: DbSession, file: Annotated[UploadFile, File()]
) -> ExamImportResult:
    """Import a whole exam from a file produced by :func:`export_exam`.

    Always creates a **new** exam — a restore/transfer, never a merge into an existing one.
    ``lecture_name`` is resolved against the caller's **own** lectures only, by an exact,
    case-sensitive name match; if none matches, a new ``Lecture`` is created for it. Scoping the
    lookup to the caller's own lectures matters for two reasons: it must never attach the
    imported exam to another instructor's lecture, and it must not even reveal whether a
    same-named lecture belonging to someone else exists (the usual 404-not-403 posture — see
    this module's docstring).

    The new exam's owner is always the **importer**, never a value read from the file — see
    ``ExamExportPayload``'s docstring for why honoring a file-provided owner would be a
    privilege hole.

    Everything is validated — file readability, JSON structure, exercises/grading-schema business
    rules (the same ones ``create_exam`` enforces), within-file duplicate Matrikelnummer, and
    every points entry naming a real exercise of this file with a non-negative value — before
    anything is written to the database, the same all-or-nothing posture as the registration-PDF
    import (§5.3).
    """
    try:
        raw = json.loads(file.file.read())
    except (json.JSONDecodeError, UnicodeDecodeError):
        _raise_validation_errors([IMPORT_INVALID_JSON_DETAIL])
        raise AssertionError("unreachable") from None  # pragma: no cover

    try:
        payload = ExamExportPayload.model_validate(raw)
    except ValidationError as exc:
        _raise_validation_errors(_format_import_structure_errors(exc))
        raise AssertionError("unreachable") from None  # pragma: no cover

    errors: list[str] = []
    if payload.format_version != 1:
        errors.append(IMPORT_FORMAT_VERSION_DETAIL)
    if not payload.lecture_name.strip():
        errors.append(LECTURE_NAME_REQUIRED_DETAIL)
    if not payload.semester.strip():
        errors.append(SEMESTER_REQUIRED_DETAIL)
    if not payload.termin.strip():
        errors.append(TERMIN_REQUIRED_DETAIL)
    if payload.bonus_points < 0:
        errors.append(BONUS_POINTS_NEGATIVE_DETAIL)

    exercises = [
        ExerciseInput(name=item.name, max_points=item.max_points) for item in payload.exercises
    ]
    errors.extend(_validate_exercises(exercises))

    schema_input = [
        GradeThresholdInput(grade=item.grade, percentage=item.percentage)
        for item in payload.grading_schema
    ]
    percentages, schema_errors = _validate_grading_schema_input(schema_input)
    errors.extend(schema_errors)

    # §5.3's "never silently merged or duplicated" rule, applied within this one file — there is
    # no existing-registrations DB check to make here, since import always creates a brand-new
    # exam that starts with none.
    seen_counts: dict[str, int] = {}
    for registration in payload.registrations:
        key = registration.matrikelnummer.strip()
        seen_counts[key] = seen_counts.get(key, 0) + 1
    duplicates = sorted(
        matrikelnummer for matrikelnummer, count in seen_counts.items() if count > 1
    )
    if duplicates:
        errors.append(
            "Doppelte Matrikelnummer(n) in der Exportdatei: " + ", ".join(duplicates) + "."
        )

    exercise_count = len(payload.exercises)
    for registration in payload.registrations:
        for key, value in registration.points.items():
            try:
                index = int(key)
            except ValueError:
                errors.append(
                    f"{registration.matrikelnummer}: Ungültiger Aufgaben-Index „{key}“ in den "
                    "Punkten."
                )
                continue
            if not 1 <= index <= exercise_count:
                errors.append(
                    f"{registration.matrikelnummer}: Aufgabe Nr. {index} in den Punkten kommt in "
                    "dieser Datei nicht vor."
                )
                continue
            if value < 0:
                errors.append(
                    f"{registration.matrikelnummer}: Punkte für Aufgabe Nr. {index} dürfen nicht "
                    "negativ sein."
                )

    # Everything above is validated before anything below is mutated, so a rejected import leaves
    # the database exactly as it was (same posture as create_exam/update_exam).
    _raise_validation_errors(errors)

    lecture_name = payload.lecture_name.strip()
    lecture = (
        db.execute(select(Lecture).where(Lecture.owner_id == user.id, Lecture.name == lecture_name))
        .scalars()
        .first()
    )
    lecture_created = lecture is None
    if lecture is None:
        lecture = Lecture(name=lecture_name, owner_id=user.id)
        db.add(lecture)
        db.flush()

    exam = Exam(
        lecture_id=lecture.id,
        owner_id=user.id,
        semester=payload.semester.strip(),
        termin=payload.termin.strip(),
        exam_date=payload.exam_date,
        bonus_mode=payload.bonus_mode,
        bonus_points=payload.bonus_points,
    )
    db.add(exam)
    db.flush()
    _replace_exercises(db, exam, exercises)
    _replace_grading_schema(db, exam, percentages)
    db.flush()

    # `_replace_exercises` appends every (new, since none of `exercises` carries an `id`) exercise
    # in submitted order, i.e. `payload.exercises`'s own order — exactly the 1-based index
    # `points` keys reference, already bounds-checked against `exercise_count` above.
    exercise_by_index = dict(enumerate(exam.exercises, start=1))

    for registration in payload.registrations:
        row = StudentRegistration(
            exam_id=exam.id,
            matrikelnummer=registration.matrikelnummer.strip(),
            nachname=registration.nachname.strip(),
            vorname=registration.vorname.strip(),
            course_code=registration.course_code.strip(),
            module_title=registration.module_title.strip(),
            versuch=registration.versuch,
            kommentar=registration.kommentar,
            flagged=registration.flagged,
            excluded=registration.excluded,
            attended=registration.attended,
            source_filename=registration.source_filename,
        )
        db.add(row)
        db.flush()
        for key, value in registration.points.items():
            exercise = exercise_by_index[int(key)]
            db.add(ExercisePoints(registration_id=row.id, exercise_id=exercise.id, points=value))

    db.commit()
    db.refresh(exam)
    return ExamImportResult(
        exam=exam_detail(db, exam),
        lecture_created=lecture_created,
        registrations_imported=len(payload.registrations),
    )
