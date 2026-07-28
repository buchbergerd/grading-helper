"""Grade computation: bonus modes, attendance interaction, completeness (§7.3-§7.5, §8.1).

Pure functions over :class:`~decimal.Decimal`. **No database, no FastAPI, no ORM imports** — the
engine takes plain values so it can be tested exhaustively and reused unchanged by the statistics
module (§9) and the report generators (§10, §11). Wiring it to the API is a separate concern.

Design notes an implementer needs to know:

* **Never ``float``** (§7.0). A ``float`` argument is refused with :class:`TypeError` rather than
  silently converted — ``Decimal(0.75)`` yields ``0.74999999999999988897…``, which is exactly the
  silent corruption §7.0 forbids. This mirrors :mod:`app.grading.schema` and :mod:`app.types`.
* ``thresholds`` is always the **stored form: per-grade percentages**, and this module calls
  :func:`app.grading.schema.threshold_points` itself. Callers must never pre-convert.
* ``bonus_mode`` is a plain :class:`str` compared against :data:`BONUS_MODE_ALWAYS` /
  :data:`BONUS_MODE_ONLY_IF_PASSING_WITHOUT_BONUS`. ``app.models.exam.BonusMode`` is a
  :class:`~enum.StrEnum` whose values equal these constants, so its members can be passed
  directly — without this module importing the ORM.
* Error messages here are **English**: unlike ``validate_grading_schema``'s German output, these
  signal programming errors that must never reach a user, not input to be reported back.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from app.grading.schema import GRADES, PASSING_GRADE, threshold_points

#: ``final_total = raw_total + bonus_points``, uncapped (§7.3).
BONUS_MODE_ALWAYS = "ALWAYS"
#: Bonus counts only if ``raw_total`` *alone* already meets the 4.0 threshold (§7.3).
BONUS_MODE_ONLY_IF_PASSING_WITHOUT_BONUS = "ONLY_IF_PASSING_WITHOUT_BONUS"

_BONUS_MODES = frozenset({BONUS_MODE_ALWAYS, BONUS_MODE_ONLY_IF_PASSING_WITHOUT_BONUS})

#: Grade text for an attended student below the 4.0 threshold (§7.4). Text, never a number.
GRADE_FAILED = "nicht bestanden"
#: Grade text for a student recorded as not attended (§7.4). Overrides any points.
GRADE_NOT_ATTENDED = "n.e."


class GradeStatus(StrEnum):
    """Machine-readable outcome of :func:`compute_grade`.

    This — not :attr:`GradeResult.is_passing` — is the signal downstream code should branch on.
    The values are English tokens for internal use; user-facing output uses
    :attr:`GradeResult.grade`, which is German (:data:`GRADE_FAILED`, :data:`GRADE_NOT_ATTENDED`,
    or a numeric grade of :data:`~app.grading.schema.GRADES`).
    """

    #: Attended and passing: :attr:`GradeResult.grade` is a numeric grade, e.g. ``"2.3"``.
    GRADED = "GRADED"
    #: Attended but below the 4.0 threshold: grade is :data:`GRADE_FAILED`.
    FAILED = "FAILED"
    #: ``attended is False``: grade is :data:`GRADE_NOT_ATTENDED` (§7.4).
    NOT_ATTENDED = "NOT_ATTENDED"
    #: ``attended is None`` — attendance not yet recorded (§4), so **nothing is computable**.
    #: Deliberately distinct from :data:`NOT_ATTENDED`: §8.1 forbids guessing at missing data, so
    #: this is neither "n.e." nor a grade. :attr:`GradeResult.grade` and
    #: :attr:`GradeResult.final_total` are both ``None``.
    ATTENDANCE_NOT_RECORDED = "ATTENDANCE_NOT_RECORDED"


@dataclass(frozen=True)
class GradeResult:
    """The full, auditable outcome of grading one student (§7.3-§7.5)."""

    #: Sum of the exercise points passed in. Always computed, even when the grade is not:
    #: it is a plain fact about the entered data. Missing entries are simply absent from the
    #: input — they are **never** summed in as an implicit zero (§8.1).
    raw_total: Decimal
    #: ``raw_total`` plus the bonus if and only if :attr:`bonus_applied`. ``None`` whenever no
    #: total is meaningful, i.e. for both :attr:`GradeStatus.NOT_ATTENDED` (§7.5 prints "—" for
    #: that row) and :attr:`GradeStatus.ATTENDANCE_NOT_RECORDED`.
    final_total: Decimal | None
    #: The user-facing German grade text, or ``None`` for
    #: :attr:`GradeStatus.ATTENDANCE_NOT_RECORDED` (nothing to display yet).
    grade: str | None
    #: The outcome category. Branch on this, not on :attr:`grade` string comparisons.
    status: GradeStatus
    #: Whether the bonus was counted **per the §7.3 rule**, independent of its magnitude: it is
    #: ``True`` for a bonus of 0 whose mode gate passed, and ``False`` exactly when the rule
    #: withheld the bonus (or no grade was computed at all). ``bonus_applied is False`` together
    #: with a non-zero bonus is the "Bonus wurde nicht angerechnet" case the UI explains.
    bonus_applied: bool
    #: ``True`` only for :attr:`GradeStatus.GRADED`. Note this is **not** the fail signal:
    #: not-attended and not-yet-recorded are also ``False``.
    is_passing: bool
    #: The 4.0 point threshold this result was decided against (§7.2), for display and for §9.
    passing_threshold: Decimal


@dataclass(frozen=True)
class CompletenessResult:
    """Whether one registration is ready for the §8.1 export gate."""

    #: ``True`` when nothing is missing. Exports must refuse while any non-excluded student is
    #: incomplete — never substitute an implicit zero (§8.1).
    is_complete: bool
    #: ``attended`` has not been recorded yet (``None``).
    attendance_missing: bool
    #: Exercises with no ``ExercisePoints`` row, in the order the exercise ids were given.
    #: Always empty when the student did not attend — no points are needed then (§7.4, §8).
    missing_exercise_ids: tuple[int, ...]


def _reject_float(value: object, label: str) -> None:
    """Refuse a ``float`` where an exact decimal is required (§7.0)."""
    if isinstance(value, float):
        raise TypeError(
            f"{label} is a float ({value!r}); §7.0 requires exact Decimal arithmetic. "
            "Pass a Decimal, int or str-parsed Decimal instead."
        )


def _validate_thresholds(thresholds: Mapping[str, Decimal], max_points: Decimal) -> None:
    """Check the schema mapping and its values before any grade is derived.

    All ten grades must be present. A missing key would silently award a *worse* grade than
    earned with no error anywhere — so this fails loudly instead, matching this package's
    posture on floats. Every exam carries all ten ``GradeThreshold`` rows, so this costs a
    legitimate caller nothing.
    """
    _reject_float(max_points, "max_points")
    for grade, percentage in thresholds.items():
        _reject_float(percentage, f"Threshold percentage for grade {grade!r}")
    missing = [grade for grade in GRADES if grade not in thresholds]
    if missing:
        raise ValueError(
            "thresholds must contain all ten grades of the §7.1 scale as percentages; "
            f"missing: {', '.join(missing)}."
        )


def point_thresholds(thresholds: Mapping[str, Decimal], max_points: Decimal) -> dict[str, Decimal]:
    """Convert the stored per-grade **percentages** into point thresholds (§7.2).

    Keyed by grade, in :data:`~app.grading.schema.GRADES` order (best to worst). Note that two
    adjacent grades can legitimately share a point threshold once flooring to 0.5 collapses
    them (e.g. 51 % and 50 % of 10 points are both 5.0); grade assignment iterates best to worst
    and so resolves such a tie in the student's favour.
    """
    _validate_thresholds(thresholds, max_points)
    return {grade: threshold_points(thresholds[grade], max_points) for grade in GRADES}


def passing_threshold_points(thresholds: Mapping[str, Decimal], max_points: Decimal) -> Decimal:
    """The 4.0 point threshold — the pass/fail line used by §7.3, §7.4, §8.1 and §9."""
    _validate_thresholds(thresholds, max_points)
    return threshold_points(thresholds[PASSING_GRADE], max_points)


def grade_for_total(
    total: Decimal, thresholds: Mapping[str, Decimal], max_points: Decimal
) -> str | None:
    """The best (numerically lowest) grade whose threshold ``total`` meets, or ``None`` if none.

    "Met" is ``total >= threshold`` — exactly hitting a threshold earns that grade (§7.5: 30.0
    against a 30.0 threshold is a 4.0, not a fail). ``None`` means below the 4.0 threshold, i.e.
    failed; this function knows nothing about attendance, which overrides it (§7.4).
    """
    _reject_float(total, "total")
    for grade, points in point_thresholds(thresholds, max_points).items():
        if total >= points:
            return grade
    return None


def compute_grade(
    *,
    exercise_points: Iterable[Decimal],
    bonus_points: Decimal,
    attended: bool | None,
    bonus_mode: str,
    thresholds: Mapping[str, Decimal],
    max_points: Decimal,
) -> GradeResult:
    """Grade one student (§7.2-§7.5).

    :param exercise_points: the points **actually entered**, one per entry. An exercise with no
        ``ExercisePoints`` row must simply be omitted — never passed as ``Decimal(0)`` (§8.1).
        Use :func:`check_completeness` to decide whether the input is complete enough to export.
    :param bonus_points: per-student bonus, ``Decimal(0)`` if none (§7.3).
    :param attended: ``True``/``False`` as recorded, ``None`` if not yet recorded (§4).
    :param bonus_mode: :data:`BONUS_MODE_ALWAYS` or
        :data:`BONUS_MODE_ONLY_IF_PASSING_WITHOUT_BONUS`; anything else raises
        :class:`ValueError` rather than falling through to a default.
    :param thresholds: the exam's stored per-grade **percentages**, all ten grades.
    :param max_points: the exam's total maximum points (sum of all exercises' ``max_points``).

    Attendance is resolved first and overrides everything (§7.4). Totals are exact: nothing is
    rounded or quantized, so ``Decimal("0.1") + Decimal("0.2")`` is ``Decimal("0.3")`` here.
    """
    if bonus_mode not in _BONUS_MODES:
        raise ValueError(
            f"Unknown bonus_mode {bonus_mode!r}; expected one of {sorted(_BONUS_MODES)}. "
            "Defaulting silently would let a withheld bonus be applied anyway (§7.3)."
        )
    _reject_float(bonus_points, "bonus_points")

    raw_total = Decimal(0)
    for index, points in enumerate(exercise_points):
        _reject_float(points, f"exercise_points[{index}]")
        raw_total += points

    passing_threshold = passing_threshold_points(thresholds, max_points)

    if attended is None:
        # §4/§8.1: not recorded is not the same as "did not attend", and it is not a zero.
        # Return an explicit "not yet computable" result rather than guessing at either.
        return GradeResult(
            raw_total=raw_total,
            final_total=None,
            grade=None,
            status=GradeStatus.ATTENDANCE_NOT_RECORDED,
            bonus_applied=False,
            is_passing=False,
            passing_threshold=passing_threshold,
        )
    if attended is False:
        # §7.4: "n.e.", full stop — no points needed or used, however many were entered.
        return GradeResult(
            raw_total=raw_total,
            final_total=None,
            grade=GRADE_NOT_ATTENDED,
            status=GradeStatus.NOT_ATTENDED,
            bonus_applied=False,
            is_passing=False,
            passing_threshold=passing_threshold,
        )
    if attended is not True:
        raise TypeError(f"attended must be True, False or None; got {attended!r}.")

    # §7.3. ONLY_IF_PASSING_WITHOUT_BONUS asks whether raw_total *alone* already passes, and
    # decides from that whether the bonus is added at all. It is emphatically NOT "add the bonus,
    # then cap the result at the pass mark": under this mode a passing student's bonus still
    # improves the grade without limit, while a failing student's bonus is discarded outright.
    bonus_withheld = (
        bonus_mode == BONUS_MODE_ONLY_IF_PASSING_WITHOUT_BONUS and raw_total < passing_threshold
    )
    bonus_applied = not bonus_withheld
    final_total = raw_total + bonus_points if bonus_applied else raw_total

    grade = grade_for_total(final_total, thresholds, max_points)
    if grade is None:
        return GradeResult(
            raw_total=raw_total,
            final_total=final_total,
            grade=GRADE_FAILED,
            status=GradeStatus.FAILED,
            bonus_applied=bonus_applied,
            is_passing=False,
            passing_threshold=passing_threshold,
        )
    return GradeResult(
        raw_total=raw_total,
        final_total=final_total,
        grade=grade,
        status=GradeStatus.GRADED,
        bonus_applied=bonus_applied,
        is_passing=True,
        passing_threshold=passing_threshold,
    )


def check_completeness(
    *,
    attended: bool | None,
    exercise_ids: Sequence[int],
    entered_exercise_ids: Collection[int],
) -> CompletenessResult:
    """Is one registration complete enough for the §8.1 export gate?

    :param attended: as recorded; ``None`` means not yet recorded and is always incomplete.
    :param exercise_ids: every exercise of the exam.
    :param entered_exercise_ids: the exercises this student has an ``ExercisePoints`` row for.
        Membership is what counts, not the value: an entered ``Decimal("0")`` is complete, while
        an absent row is missing and must never be read as a zero.

    A student recorded as not attended is complete with no points at all (§7.4, §8).
    """
    if attended is None:
        return CompletenessResult(
            is_complete=False, attendance_missing=True, missing_exercise_ids=()
        )
    if attended is False:
        return CompletenessResult(
            is_complete=True, attendance_missing=False, missing_exercise_ids=()
        )
    if attended is not True:
        raise TypeError(f"attended must be True, False or None; got {attended!r}.")

    missing = tuple(
        exercise_id for exercise_id in exercise_ids if exercise_id not in entered_exercise_ids
    )
    return CompletenessResult(
        is_complete=not missing, attendance_missing=False, missing_exercise_ids=missing
    )
