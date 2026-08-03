"""Shared per-student grade computation for the §10/§11 reports.

Both ``examination_office.py`` and ``student_results.py`` need the exact same "Note" cell for a
non-excluded registration — a numeric grade, ``"nicht bestanden"``, or ``"n.e."`` (§7.4) — so it
is factored here once rather than derived twice, the same way ``attendance_list.py`` promoted its
filename helpers to public names for ``internal_report.py`` to reuse (see that module's
docstring).

**This module formats the German comma itself** (:func:`student_note` returns display-ready
text, e.g. ``"1,3"``), unlike ``app/statistics.py``'s payload, which stays in canonical dot form
and lets each renderer comma-ify it. That split exists there because one payload feeds two
consumers with genuinely different needs — the JSON API (§7.0's canonical contract) and the Typst
PDF (§14 #6). §10/§11 have no JSON consumer: the payload this module's callers build feeds only
Typst and openpyxl, both German-facing, so formatting once here — instead of once per renderer —
removes a chance for the PDF and the Excel export of the same report to disagree.

``thresholds_or_none``/``total_max_points`` mirror ``app.api.points``'s and ``app.statistics``'s
private helpers of the same purpose, duplicated rather than imported for the same reason
``app/statistics.py`` gives for its own copy: a core/report module must not depend on
``app.api`` (that would invert the codebase's one-directional dependency direction), and
``app/statistics.py`` itself must not become a dependency of unrelated report modules either.
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal

from app.formatting import format_german_decimal
from app.grading.engine import GradeStatus, compute_grade
from app.grading.schema import GRADES
from app.models import BonusMode, Exam, StudentRegistration


def thresholds_or_none(exam: Exam) -> dict[str, Decimal] | None:
    """The exam's stored per-grade percentages, or ``None`` if the schema is absent/incomplete.

    All ten §7.1 grades must be present before any grade can be computed (see
    ``app.api.points._thresholds_or_none`` / ``app.statistics._thresholds_or_none`` for the same
    check, same reasoning).
    """
    if len(exam.grade_thresholds) != len(GRADES):
        return None
    return {threshold.grade: threshold.percentage for threshold in exam.grade_thresholds}


def total_max_points(exam: Exam) -> Decimal:
    """Sum of the exam's exercises' ``max_points``, summed in Python over decoded ``Decimal``s."""
    return sum((exercise.max_points for exercise in exam.exercises), Decimal(0))


def student_note(
    registration: StudentRegistration,
    *,
    exercise_ids: Sequence[int],
    thresholds: dict[str, Decimal],
    max_points: Decimal,
    bonus_mode: BonusMode,
    bonus_points: Decimal,
) -> str:
    """The registration's §10/§11 "Note" cell, already German-formatted (see module docstring).

    Requires the §8.1 completeness gate to already have passed for this registration — ``attended``
    recorded, and if ``True``, every exercise in ``exercise_ids`` entered — and a fully configured
    grading schema (``thresholds`` from :func:`thresholds_or_none`, not ``None``). Both are the
    calling route's responsibility (``app/api/reports.py``'s export gate), not this function's:
    raises :class:`ValueError` rather than silently emitting ``"n.e."``/``None`` for a student who
    simply hasn't been graded yet, so a caller that forgets the gate fails loudly instead of
    exporting a wrong document.
    """
    if registration.attended is None:
        raise ValueError(
            f"{registration.matrikelnummer}: attendance not recorded; the §8.1 gate must run "
            "before student_note is called."
        )
    entered = {points.exercise_id: points.points for points in registration.exercise_points}
    if registration.attended is True:
        missing = [exercise_id for exercise_id in exercise_ids if exercise_id not in entered]
        if missing:
            raise ValueError(
                f"{registration.matrikelnummer}: missing points for exercise id(s) {missing}; "
                "the §8.1 gate must run before student_note is called."
            )
    entered_in_order = [
        entered[exercise_id] for exercise_id in exercise_ids if exercise_id in entered
    ]

    result = compute_grade(
        exercise_points=entered_in_order,
        bonus_points=bonus_points,
        attended=registration.attended,
        bonus_mode=bonus_mode,
        thresholds=thresholds,
        max_points=max_points,
    )
    if result.status is GradeStatus.GRADED:
        return format_german_decimal(Decimal(result.grade))  # type: ignore[arg-type]
    # FAILED -> "nicht bestanden", NOT_ATTENDED -> "n.e." (§7.4) — already the right display text.
    assert result.grade is not None  # guaranteed: attended was checked above, never None here
    return result.grade
