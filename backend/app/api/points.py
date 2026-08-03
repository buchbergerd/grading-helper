"""Points/attendance entry and the §8.1 completeness gate (SPECIFICATION.md §8, §8.1, §7.4).

This module owns all four points routes plus the helpers shared with the future §10/§11 report
endpoints (:func:`exam_completeness`) and with ``app/api/exams.py``'s §8.1 recomputation hook
(:func:`grade_snapshot`). It imports :func:`app.api.exams.get_owned_exam` and
:func:`app.api.exams.total_max_points`, and :func:`app.api.registrations.get_owned_registration`
— both edges are one-directional (``points`` depends on ``exams``/``registrations``, never back).
``app/api/exams.py`` closes the §8.1 loop with a **function-local** import of this module, which
is what breaks the cycle: at *module* import time ``exams.py`` never touches ``points.py``.

Two invariants this module must never violate (CLAUDE.md, §8.1):

* the absence of an ``ExercisePoints`` row means "not entered"; a row holding ``Decimal("0")``
  means "entered zero" — never conflate the two, and never create a row with a default;
* when the grading schema is absent or incomplete, grade computation is simply skipped
  (``grade``/``status`` are ``null``, ``grading_configured`` is ``False``) rather than calling
  :func:`~app.grading.engine.compute_grade`, which requires all ten §7.1 grades and raises
  ``ValueError`` otherwise — that would surface as an unhandled ``500``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from decimal import Decimal

from fastapi import APIRouter, HTTPException, Query

from app.api.exams import get_owned_exam, total_max_points
from app.api.registrations import get_owned_registration
from app.api.schemas import (
    BulkPointsSaveRequest,
    BulkPointsSaveResult,
    CompletenessOut,
    ExerciseOut,
    GradeThresholdOut,
    IncompleteStudentOut,
    PointsEntryOut,
    PointsGridOut,
    PointsSaveRequest,
    PointsSaveResult,
)
from app.auth.dependencies import CurrentUser, DbSession
from app.grading.engine import check_completeness, compute_grade
from app.grading.schema import GRADES, threshold_points
from app.models import BonusMode, Exam, Exercise, ExercisePoints, StudentRegistration

router = APIRouter(tags=["points"])


def _raise_validation_errors(errors: list[str]) -> None:
    """Raise the contract's ``422`` shape (``{"detail": {"errors": [...]}}``) if non-empty."""
    if errors:
        raise HTTPException(status_code=422, detail={"errors": errors})


# --------------------------------------------------------------------------------------------
# Shared read helpers
# --------------------------------------------------------------------------------------------


def _thresholds_or_none(exam: Exam) -> dict[str, Decimal] | None:
    """The exam's stored percentages, or ``None`` if the schema is absent/incomplete (§7.2).

    ``compute_grade`` requires all ten §7.1 grades; an exam may legitimately have zero (never
    configured) or, only transiently mid-edit, something other than ten — never trust the count
    without checking it equals the full scale.
    """
    if len(exam.grade_thresholds) != len(GRADES):
        return None
    return {threshold.grade: threshold.percentage for threshold in exam.grade_thresholds}


def _grading_schema_out(exam: Exam, max_points: Decimal) -> list[GradeThresholdOut]:
    """The exam's schema in §7.1 grade order, each with its server-computed point threshold."""
    by_grade = {threshold.grade: threshold.percentage for threshold in exam.grade_thresholds}
    return [
        GradeThresholdOut(
            grade=grade,
            percentage=by_grade[grade],
            threshold_points=threshold_points(by_grade[grade], max_points),
        )
        for grade in GRADES
        if grade in by_grade
    ]


def registration_points_row(
    registration: StudentRegistration,
    exercises: Sequence[Exercise],
    thresholds: Mapping[str, Decimal] | None,
    max_points: Decimal,
    bonus_mode: BonusMode,
    bonus_points: Decimal,
) -> PointsEntryOut:
    """One registration's row for the §8 grid, or the response of a points save.

    :param thresholds: from :func:`_thresholds_or_none` — ``None`` skips grade computation
        entirely rather than calling :func:`~app.grading.engine.compute_grade` on an incomplete
        schema (see this module's docstring).
    :param bonus_points: the exam's single amount (§7.3) — not per registration.
    """
    entered = {points.exercise_id: points.points for points in registration.exercise_points}
    exercise_ids = [exercise.id for exercise in exercises]
    entered_in_order = [
        entered[exercise_id] for exercise_id in exercise_ids if exercise_id in entered
    ]
    completeness = check_completeness(
        attended=registration.attended,
        exercise_ids=exercise_ids,
        entered_exercise_ids=entered.keys(),
    )

    raw_total: Decimal
    final_total: Decimal | None
    grade: str | None
    status_token: str | None
    if thresholds is not None:
        result = compute_grade(
            exercise_points=entered_in_order,
            bonus_points=bonus_points,
            attended=registration.attended,
            bonus_mode=bonus_mode,
            thresholds=thresholds,
            max_points=max_points,
        )
        raw_total = result.raw_total
        final_total = result.final_total
        grade = result.grade
        status_token = result.status.value
    else:
        raw_total = sum(entered_in_order, Decimal(0))
        final_total = None
        grade = None
        status_token = None

    return PointsEntryOut(
        id=registration.id,
        matrikelnummer=registration.matrikelnummer,
        nachname=registration.nachname,
        vorname=registration.vorname,
        course_code=registration.course_code,
        versuch=registration.versuch,
        attended=registration.attended,
        points={str(exercise_id): value for exercise_id, value in entered.items()},
        raw_total=raw_total,
        final_total=final_total,
        grade=grade,
        status=status_token,
        is_complete=completeness.is_complete,
    )


def exam_completeness(exam: Exam) -> CompletenessOut:
    """The §8.1 export-readiness gate for one exam.

    Reusable on purpose: the §10/§11 report endpoints must block on exactly this check before
    generating the examination-office or student-results report, and must show the same list of
    incomplete rows this route does — hence a shared helper rather than logic duplicated (and
    liable to drift) inline in each route.

    Excluded students are never counted (§5.3: they never receive a grade). Sorted by
    Matrikelnummer for a stable, readable list.
    """
    exercise_ids = [exercise.id for exercise in exam.exercises]
    exercise_names = {exercise.id: exercise.name for exercise in exam.exercises}

    incomplete: list[IncompleteStudentOut] = []
    for registration in exam.registrations:
        if registration.excluded:
            continue
        entered_ids = {points.exercise_id for points in registration.exercise_points}
        result = check_completeness(
            attended=registration.attended,
            exercise_ids=exercise_ids,
            entered_exercise_ids=entered_ids,
        )
        if result.is_complete:
            continue
        incomplete.append(
            IncompleteStudentOut(
                id=registration.id,
                matrikelnummer=registration.matrikelnummer,
                nachname=registration.nachname,
                vorname=registration.vorname,
                attendance_missing=result.attendance_missing,
                missing_exercises=[
                    exercise_names[exercise_id] for exercise_id in result.missing_exercise_ids
                ],
            )
        )
    incomplete.sort(key=lambda item: item.matrikelnummer)

    return CompletenessOut(
        is_complete=not incomplete,
        incomplete_count=len(incomplete),
        incomplete_students=incomplete,
    )


def grade_snapshot(exam: Exam) -> dict[int, str | None]:
    """The **computed** grade string of every non-excluded registration, right now.

    Used exclusively by ``app/api/exams.py``'s §8.1 recomputation hook: called once before an
    exercises/grading-schema edit and once after, so the caller can count how many registrations'
    grade *string* actually differs — never mind how many merely carry data. ``None`` covers both
    "schema not (fully) configured" and "attendance not yet recorded"; both are legitimately
    "nothing to compare yet" and a transition into or out of ``None`` still counts as a change.

    Nothing is written here — this is a pure read over whatever ``exam``'s relationships
    currently report, which is why the caller is responsible for making sure those relationships
    reflect the state it wants a snapshot of (pre- or post-mutation).
    """
    thresholds = _thresholds_or_none(exam)
    max_points = total_max_points(exam)
    exercise_ids = [exercise.id for exercise in exam.exercises]

    snapshot: dict[int, str | None] = {}
    for registration in exam.registrations:
        if registration.excluded:
            continue
        if thresholds is None:
            snapshot[registration.id] = None
            continue
        entered = {points.exercise_id: points.points for points in registration.exercise_points}
        entered_in_order = [
            entered[exercise_id] for exercise_id in exercise_ids if exercise_id in entered
        ]
        result = compute_grade(
            exercise_points=entered_in_order,
            bonus_points=exam.bonus_points,
            attended=registration.attended,
            bonus_mode=exam.bonus_mode,
            thresholds=thresholds,
            max_points=max_points,
        )
        snapshot[registration.id] = result.grade
    return snapshot


# --------------------------------------------------------------------------------------------
# Save validation and mutation (shared by the single-row and bulk routes)
# --------------------------------------------------------------------------------------------


def _validate_points_payload(
    exam: Exam, registration: StudentRegistration, payload: PointsSaveRequest
) -> list[str]:
    """German validation errors for one row (empty = valid). Never mutates anything.

    §5.3/§8: an excluded registration never receives a grade, so writing points to one is
    rejected outright rather than silently accepted and ignored. Negative points are rejected
    (§8 only asks for a warning on *exceeding* ``max_points``, never on going negative).
    """
    if registration.excluded:
        return [
            f"{registration.matrikelnummer}: Ausgeschlossene Anmeldungen erhalten keine Punkte."
        ]

    errors: list[str] = []
    exercises_by_id = {exercise.id: exercise for exercise in exam.exercises}
    for key, value in payload.points.items():
        try:
            exercise_id = int(key)
        except ValueError:
            errors.append(f"{registration.matrikelnummer}: Ungültige Aufgaben-ID „{key}“.")
            continue
        exercise = exercises_by_id.get(exercise_id)
        if exercise is None:
            errors.append(
                f"{registration.matrikelnummer}: Aufgabe {key} gehört nicht zu dieser Prüfung."
            )
            continue
        if value is not None and value < 0:
            errors.append(
                f"{registration.matrikelnummer}: Punkte für Aufgabe „{exercise.name}“ dürfen "
                "nicht negativ sein."
            )
    return errors


def _apply_points_save(
    exam: Exam, registration: StudentRegistration, payload: PointsSaveRequest
) -> list[str]:
    """Apply the full-replace save to ``registration`` in place; return non-blocking warnings.

    Must only be called after :func:`_validate_points_payload` returned no errors. Iterates over
    the exam's own exercises — not the payload's keys — so that an exercise simply missing from
    ``payload.points`` (as opposed to present with ``null``) is deleted exactly the same way
    (§8's PUT is a full replace, not a merge): both mean "not entered".
    """
    registration.attended = payload.attended

    existing = {points.exercise_id: points for points in registration.exercise_points}
    warnings: list[str] = []
    for exercise in exam.exercises:
        value = payload.points.get(str(exercise.id))
        if value is None:
            row = existing.get(exercise.id)
            if row is not None:
                registration.exercise_points.remove(row)
            continue
        if value > exercise.max_points:
            warnings.append(
                f"{registration.matrikelnummer}: Aufgabe „{exercise.name}“ überschreitet die "
                f"maximale Punktzahl ({value} von {exercise.max_points})."
            )
        row = existing.get(exercise.id)
        if row is not None:
            row.points = value
        else:
            registration.exercise_points.append(
                ExercisePoints(exercise_id=exercise.id, points=value)
            )
    return warnings


# --------------------------------------------------------------------------------------------
# Routes
# --------------------------------------------------------------------------------------------


@router.get("/exams/{exam_id}/points", response_model=PointsGridOut)
def read_points_grid(
    exam_id: int,
    user: CurrentUser,
    db: DbSession,
    course_code: str | None = Query(default=None),
) -> PointsGridOut:
    """Everything the §8 entry grid needs in one call.

    One row per **non-excluded** registration (§5.3 — an excluded student never receives a
    grade), sorted by Matrikelnummer. When the exam's grading schema is absent or incomplete,
    every row's ``grade``/``status`` are ``null`` and ``grading_configured`` is ``False`` rather
    than the route failing — a fresh exam with points already being entered but no schema yet is
    a normal, not an error, state.
    """
    exam = get_owned_exam(db, user, exam_id)
    thresholds = _thresholds_or_none(exam)
    max_points = total_max_points(exam)

    registrations = [item for item in exam.registrations if not item.excluded]
    if course_code is not None:
        registrations = [item for item in registrations if item.course_code == course_code]
    registrations.sort(key=lambda item: item.matrikelnummer)

    entries = [
        registration_points_row(
            item, exam.exercises, thresholds, max_points, exam.bonus_mode, exam.bonus_points
        )
        for item in registrations
    ]
    return PointsGridOut(
        exercises=[ExerciseOut.model_validate(exercise) for exercise in exam.exercises],
        grading_schema=_grading_schema_out(exam, max_points),
        bonus_mode=exam.bonus_mode,
        bonus_points=exam.bonus_points,
        grading_configured=thresholds is not None,
        entries=entries,
    )


@router.get("/exams/{exam_id}/completeness", response_model=CompletenessOut)
def read_completeness(exam_id: int, user: CurrentUser, db: DbSession) -> CompletenessOut:
    """§8.1's export gate: is this exam ready for the §10/§11 reports?

    Blocks nothing itself — it only reports the answer and, when incomplete, the specific list of
    non-excluded students still missing attendance or (for an attended student) specific
    exercises by name, so the instructor knows exactly what to finish. The §10/§11 report routes
    call :func:`exam_completeness` directly rather than this HTTP layer.
    """
    exam = get_owned_exam(db, user, exam_id)
    return exam_completeness(exam)


@router.put("/registrations/{registration_id}/points", response_model=PointsSaveResult)
def save_points(
    registration_id: int, payload: PointsSaveRequest, user: CurrentUser, db: DbSession
) -> PointsSaveResult:
    """Save one registration's attendance/points — a **full replace**, not a merge (§8).

    * a ``points`` entry that is absent from the payload, or present with JSON ``null``,
      **deletes** that exercise's ``ExercisePoints`` row — never coerced to a stored zero, since
      §8.1 requires "not entered" and "entered zero" to stay distinguishable;
    * an absent ``attended`` sets it to ``null`` ("not yet recorded", §4).

    Marking ``attended = false`` does **not** clear previously entered points: whatever the
    payload's ``points`` map says about each exercise is applied exactly as it would be for any
    other save. In practice this means a client that resends the same ``points`` map while
    flipping ``attended`` to ``false`` keeps that data in the database — flipping back to ``true``
    later does not require re-transcribing the exam — while §7.4 makes sure those points play no
    role in the grade (``"n.e."``) for as long as ``attended`` stays ``false``.

    Points entered above an exercise's ``max_points`` are saved and reported back as a
    **warning**, never rejected or silently clamped (§8: "typos happen"). Negative points, or any
    write to an **excluded** registration, are rejected with `422` (an excluded student never
    receives a grade, §5.3). ``bonus_points`` is not part of this payload — it is the exam's
    single amount, edited via ``PATCH /api/exams/{id}`` (§7.3).
    """
    registration = get_owned_registration(db, user, registration_id)
    exam = registration.exam

    _raise_validation_errors(_validate_points_payload(exam, registration, payload))
    warnings = _apply_points_save(exam, registration, payload)
    db.commit()
    db.refresh(registration)

    thresholds = _thresholds_or_none(exam)
    max_points = total_max_points(exam)
    row = registration_points_row(
        registration, exam.exercises, thresholds, max_points, exam.bonus_mode, exam.bonus_points
    )
    return PointsSaveResult(registration=row, warnings=warnings)


@router.put("/exams/{exam_id}/points", response_model=BulkPointsSaveResult)
def save_points_bulk(
    exam_id: int, payload: BulkPointsSaveRequest, user: CurrentUser, db: DbSession
) -> BulkPointsSaveResult:
    """Save many rows in **one transaction, all-or-nothing** — per-row semantics as the single PUT.

    Every row is validated — including that its ``registration_id`` actually belongs to this exam
    and is not excluded, and that it appears at most once in the request — before anything is
    written. A single invalid row rejects the whole batch with `422` and leaves the database
    exactly as it was; nothing is committed until every row has passed.
    """
    exam = get_owned_exam(db, user, exam_id)
    registrations_by_id: dict[int, StudentRegistration] = {
        item.id: item for item in exam.registrations
    }

    errors: list[str] = []
    seen_ids: set[int] = set()
    resolved: list[tuple[StudentRegistration, PointsSaveRequest]] = []
    for entry in payload.entries:
        if entry.registration_id in seen_ids:
            errors.append(
                f"Anmeldung {entry.registration_id} kommt mehrfach im selben Speichervorgang vor."
            )
            continue
        seen_ids.add(entry.registration_id)

        registration = registrations_by_id.get(entry.registration_id)
        if registration is None:
            errors.append(f"Anmeldung {entry.registration_id} gehört nicht zu dieser Prüfung.")
            continue

        row_payload = PointsSaveRequest(attended=entry.attended, points=entry.points)
        errors.extend(_validate_points_payload(exam, registration, row_payload))
        resolved.append((registration, row_payload))

    _raise_validation_errors(errors)

    warnings: list[str] = []
    for registration, row_payload in resolved:
        warnings.extend(_apply_points_save(exam, registration, row_payload))
    db.commit()

    thresholds = _thresholds_or_none(exam)
    max_points = total_max_points(exam)
    entries_out = [
        registration_points_row(
            registration, exam.exercises, thresholds, max_points, exam.bonus_mode, exam.bonus_points
        )
        for registration, _ in resolved
    ]
    return BulkPointsSaveResult(entries=entries_out, warnings=warnings)
