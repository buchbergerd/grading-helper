"""Tests for the grade-computation engine (SPECIFICATION.md §7.3-§7.5, §8.1).

§7.5's worked example is the acceptance-test spec for §7 and is encoded verbatim first, before
anything else. The rest goes beyond it — chiefly the boundary triples around all ten thresholds,
since an off-by-one at a grade boundary is the exact bug class this module is exposed to.
"""

from __future__ import annotations

import math
from decimal import Decimal

import pytest

from app.grading.engine import (
    BONUS_MODE_ALWAYS,
    BONUS_MODE_ONLY_IF_PASSING_WITHOUT_BONUS,
    GRADE_FAILED,
    GRADE_NOT_ATTENDED,
    CompletenessResult,
    GradeResult,
    GradeStatus,
    check_completeness,
    compute_grade,
    grade_for_total,
    passing_threshold_points,
    point_thresholds,
)
from app.grading.schema import GRADES

BOTH_MODES = (BONUS_MODE_ALWAYS, BONUS_MODE_ONLY_IF_PASSING_WITHOUT_BONUS)

#: §7.5's exam: max_points = 60, 1.0 at 95 % (→ 57.0) and 4.0 at 50 % (→ 30.0). The eight grades
#: in between are spaced 5 % apart, which makes every point threshold distinct (57.0, 54.0, …,
#: 30.0, spaced 3.0) — required for the "just below" boundary assertions to be falsifiable.
SCHEMA = {
    "1.0": Decimal(95),
    "1.3": Decimal(90),
    "1.7": Decimal(85),
    "2.0": Decimal(80),
    "2.3": Decimal(75),
    "2.7": Decimal(70),
    "3.0": Decimal(65),
    "3.3": Decimal(60),
    "3.7": Decimal(55),
    "4.0": Decimal(50),
}
MAX_POINTS = Decimal(60)

#: The 60-point exam's expected point thresholds, best to worst.
EXPECTED_POINT_THRESHOLDS = {
    "1.0": Decimal("57.0"),
    "1.3": Decimal("54.0"),
    "1.7": Decimal("51.0"),
    "2.0": Decimal("48.0"),
    "2.3": Decimal("45.0"),
    "2.7": Decimal("42.0"),
    "3.0": Decimal("39.0"),
    "3.3": Decimal("36.0"),
    "3.7": Decimal("33.0"),
    "4.0": Decimal("30.0"),
}


def grade_of(
    raw_total: Decimal,
    *,
    bonus: Decimal = Decimal(0),
    mode: str = BONUS_MODE_ALWAYS,
    attended: bool | None = True,
    thresholds: dict[str, Decimal] | None = None,
    max_points: Decimal = MAX_POINTS,
) -> GradeResult:
    """Grade a student on the §7.5 exam, passing ``raw_total`` as a single exercise's points."""
    return compute_grade(
        exercise_points=[raw_total],
        bonus_points=bonus,
        attended=attended,
        bonus_mode=mode,
        thresholds=SCHEMA if thresholds is None else thresholds,
        max_points=max_points,
    )


# --------------------------------------------------------------------------------------
# §7.5 worked example — the acceptance-test spec, encoded verbatim
# --------------------------------------------------------------------------------------


def test_worked_example_thresholds() -> None:
    """§7.5: 95 % of 60 → 57.0 and 50 % of 60 → 30.0."""
    points = point_thresholds(SCHEMA, MAX_POINTS)
    assert points["1.0"] == Decimal("57.0")
    assert points["4.0"] == Decimal("30.0")
    assert points == EXPECTED_POINT_THRESHOLDS
    assert list(points) == list(GRADES)
    assert passing_threshold_points(SCHEMA, MAX_POINTS) == Decimal("30.0")


@pytest.mark.parametrize("mode", BOTH_MODES)
def test_worked_example_row1_exactly_meets_passing_threshold(mode: str) -> None:
    """| 30.0 | 0 | — | true | 30.0 | **4.0** (exactly meets the 4.0 threshold) |"""
    result = grade_of(Decimal("30.0"), mode=mode)
    assert result.final_total == Decimal("30.0")
    assert result.grade == "4.0"
    assert result.status is GradeStatus.GRADED
    assert result.is_passing is True


@pytest.mark.parametrize("mode", BOTH_MODES)
def test_worked_example_row2_just_below_passing_threshold(mode: str) -> None:
    """| 29.5 | 0 | — | true | 29.5 | **nicht bestanden** (below 30.0) |"""
    result = grade_of(Decimal("29.5"), mode=mode)
    assert result.final_total == Decimal("29.5")
    assert result.grade == "nicht bestanden"
    assert result.status is GradeStatus.FAILED
    assert result.is_passing is False


@pytest.mark.parametrize("mode", BOTH_MODES)
def test_worked_example_row3_attendance_overrides_everything(mode: str) -> None:
    """| 29.5 | 0 | — | false | — | **n.e.** (attendance overrides everything) |"""
    result = grade_of(Decimal("29.5"), mode=mode, attended=False)
    assert result.final_total is None
    assert result.grade == "n.e."
    assert result.status is GradeStatus.NOT_ATTENDED
    assert result.is_passing is False


def test_worked_example_row4_always_mode_applies_bonus_unconditionally() -> None:
    """| 28.0 | 3 | ALWAYS | true | 31.0 | **4.0** (bonus applied, now clears 30.0) |"""
    result = grade_of(Decimal("28.0"), bonus=Decimal(3), mode=BONUS_MODE_ALWAYS)
    assert result.raw_total == Decimal("28.0")
    assert result.final_total == Decimal("31.0")
    assert result.grade == "4.0"
    assert result.bonus_applied is True


def test_worked_example_row5_bonus_withheld_cannot_rescue_a_fail() -> None:
    """| 28.0 | 3 | ONLY_IF_… | true | 28.0 | **nicht bestanden** (28.0 < 30.0, no bonus) |"""
    result = grade_of(
        Decimal("28.0"), bonus=Decimal(3), mode=BONUS_MODE_ONLY_IF_PASSING_WITHOUT_BONUS
    )
    assert result.raw_total == Decimal("28.0")
    assert result.final_total == Decimal("28.0")
    assert result.grade == "nicht bestanden"
    assert result.bonus_applied is False
    assert result.is_passing is False


def test_worked_example_row6_bonus_applied_still_improves_the_grade() -> None:
    """| 32.0 | 3 | ONLY_IF_… | true | 35.0 | **3.7 or better per schema** |"""
    result = grade_of(
        Decimal("32.0"), bonus=Decimal(3), mode=BONUS_MODE_ONLY_IF_PASSING_WITHOUT_BONUS
    )
    assert result.raw_total == Decimal("32.0")
    assert result.final_total == Decimal("35.0")
    assert result.bonus_applied is True
    # 35.0 meets the 3.7 threshold (33.0) but not the 3.3 one (36.0).
    assert result.grade == "3.7"
    assert result.is_passing is True


def test_worked_example_row6_regression_not_capped_at_pass() -> None:
    """ONLY_IF_PASSING_WITHOUT_BONUS is not "compute with bonus, then cap at pass" (CLAUDE.md).

    Misreading the rule that way makes the bonus invisible for an already-passing student. So the
    same raw total must produce a *strictly better* grade with a bonus than without one.
    """
    without = grade_of(
        Decimal("32.0"), bonus=Decimal(0), mode=BONUS_MODE_ONLY_IF_PASSING_WITHOUT_BONUS
    )
    with_bonus = grade_of(
        Decimal("32.0"), bonus=Decimal(3), mode=BONUS_MODE_ONLY_IF_PASSING_WITHOUT_BONUS
    )
    assert without.grade == "4.0"
    assert with_bonus.grade == "3.7"
    # "Better" on the German scale is numerically lower, and neither is capped at 4.0.
    assert GRADES.index(str(with_bonus.grade)) < GRADES.index(str(without.grade))


# --------------------------------------------------------------------------------------
# Boundary triples: every grade reachable, at / just above / just below its threshold
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("grade", GRADES)
def test_every_grade_is_reachable_exactly_at_its_threshold(grade: str) -> None:
    """Exactly meeting a threshold earns that grade — ``>=``, not ``>`` (§7.2, §7.5 row 1)."""
    result = grade_of(EXPECTED_POINT_THRESHOLDS[grade])
    assert result.grade == grade
    assert result.status is GradeStatus.GRADED


@pytest.mark.parametrize("grade", GRADES)
@pytest.mark.parametrize("delta", [Decimal("0.01"), Decimal("0.25"), Decimal("2.5")])
def test_just_above_a_threshold_still_yields_that_grade(grade: str, delta: Decimal) -> None:
    """The thresholds are 3.0 apart, so +2.5 must not spill into the next better grade."""
    result = grade_of(EXPECTED_POINT_THRESHOLDS[grade] + delta)
    assert result.grade == grade


@pytest.mark.parametrize("grade", GRADES)
@pytest.mark.parametrize("delta", [Decimal("0.01"), Decimal("0.5")])
def test_just_below_a_threshold_yields_the_next_worse_grade(grade: str, delta: Decimal) -> None:
    """One hundredth of a point below a threshold must already cost the grade.

    Points are free decimals (§7.0), so 0.01 is legal input and is the sharper off-by-one
    detector: a ``>`` / ``>=`` slip can survive a 0.5-point probe.
    """
    result = grade_of(EXPECTED_POINT_THRESHOLDS[grade] - delta)
    index = GRADES.index(grade)
    if index + 1 < len(GRADES):
        assert result.grade == GRADES[index + 1]
        assert result.status is GradeStatus.GRADED
    else:
        # Below the 4.0 threshold there is no next grade — the student failed (§7.4).
        assert result.grade == GRADE_FAILED
        assert result.status is GradeStatus.FAILED


def test_best_grade_wins_when_flooring_collapses_two_thresholds() -> None:
    """Distinct percentages can floor to the same point threshold; the better grade must win."""
    schema = dict(SCHEMA)
    schema["3.7"] = Decimal("50.5")  # 50.5 % and 50 % of 10 points both floor to 5.0
    points = point_thresholds(schema, Decimal(10))
    assert points["3.7"] == points["4.0"] == Decimal("5.0")
    result = compute_grade(
        exercise_points=[Decimal("5.0")],
        bonus_points=Decimal(0),
        attended=True,
        bonus_mode=BONUS_MODE_ALWAYS,
        thresholds=schema,
        max_points=Decimal(10),
    )
    assert result.grade == "3.7"


# --------------------------------------------------------------------------------------
# §7.0 — exact decimal arithmetic
# --------------------------------------------------------------------------------------

#: The §7.0 example exam: 45 points, 4.0 at 60 %. §7.0 claims ``0.6 * 45`` is
#: ``27.000000000000004`` in IEEE-754 — **that is not true in CPython**, where ``0.6 * 45``,
#: ``45 * 0.6`` and ``60 / 100 * 45`` all evaluate to exactly ``27.0``. Kept because the spec
#: names it, but be clear that a naive float implementation *passes* this case; ``SCHEMA_50``
#: below is the one that actually separates Decimal from float.
SCHEMA_45 = {
    "1.0": Decimal(96),
    "1.3": Decimal(92),
    "1.7": Decimal(88),
    "2.0": Decimal(84),
    "2.3": Decimal(80),
    "2.7": Decimal(76),
    "3.0": Decimal(72),
    "3.3": Decimal(68),
    "3.7": Decimal(64),
    "4.0": Decimal(60),
}


#: A schema on a 50-point exam whose 4.0 threshold (29 % → exactly 14.5) is a *real* float-drift
#: case in CPython: ``math.floor((29 / 100 * 50) / 0.5) * 0.5`` is 14.0, half a point too low, so
#: float code silently passes every student between 14.0 and 14.49.
SCHEMA_50 = {
    "1.0": Decimal(92),
    "1.3": Decimal(85),
    "1.7": Decimal(78),
    "2.0": Decimal(71),
    "2.3": Decimal(64),
    "2.7": Decimal(57),
    "3.0": Decimal(50),
    "3.3": Decimal(43),
    "3.7": Decimal(36),
    "4.0": Decimal(29),
}


def test_float_error_case_from_spec_60_percent_of_45() -> None:
    """§7.0: the threshold is exactly 27.0, and a student on 27.0 passes."""
    assert passing_threshold_points(SCHEMA_45, Decimal(45)) == Decimal("27.0")

    result = compute_grade(
        exercise_points=[Decimal("27.0")],
        bonus_points=Decimal(0),
        attended=True,
        bonus_mode=BONUS_MODE_ALWAYS,
        thresholds=SCHEMA_45,
        max_points=Decimal(45),
    )
    assert result.final_total == Decimal("27.0")
    assert result.grade == "4.0"
    assert result.is_passing is True


def test_a_float_threshold_would_pass_a_failing_student() -> None:
    """The concrete drift §7.0 warns about: 29 % of 50 is 14.5 exactly, but 14.0 under floats."""
    assert math.floor((29 / 100 * 50) / 0.5) * 0.5 == 14.0  # what float code computes
    assert passing_threshold_points(SCHEMA_50, Decimal(50)) == Decimal("14.5")

    def grade_at(total: str) -> str | None:
        return compute_grade(
            exercise_points=[Decimal(total)],
            bonus_points=Decimal(0),
            attended=True,
            bonus_mode=BONUS_MODE_ALWAYS,
            thresholds=SCHEMA_50,
            max_points=Decimal(50),
        ).grade

    # Float code would award a 4.0 here; the exact threshold is half a point higher.
    assert grade_at("14.0") == GRADE_FAILED
    assert grade_at("14.49") == GRADE_FAILED
    assert grade_at("14.5") == "4.0"


def test_free_decimal_exercise_points_sum_exactly() -> None:
    """Exercise points are free decimals (§7.0): 0.75 and 12.25 must sum without drift."""
    result = compute_grade(
        exercise_points=[Decimal("0.75"), Decimal("12.25"), Decimal("9.5"), Decimal("7.5")],
        bonus_points=Decimal(0),
        attended=True,
        bonus_mode=BONUS_MODE_ALWAYS,
        thresholds=SCHEMA,
        max_points=MAX_POINTS,
    )
    assert result.raw_total == Decimal("30.00")
    assert result.grade == "4.0"


def test_classic_float_sum_is_exact_through_the_engine() -> None:
    """``0.1 + 0.2 == 0.3`` holds in Decimal, and must hold through the engine."""
    result = compute_grade(
        exercise_points=[Decimal("0.1"), Decimal("0.2"), Decimal("29.7")],
        bonus_points=Decimal(0),
        attended=True,
        bonus_mode=BONUS_MODE_ALWAYS,
        thresholds=SCHEMA,
        max_points=MAX_POINTS,
    )
    assert result.raw_total == Decimal("30.0")
    assert Decimal("0.1") + Decimal("0.2") == Decimal("0.3")
    assert result.grade == "4.0"


def test_bonus_sums_exactly_across_the_passing_boundary() -> None:
    """A 0.25 bonus is what separates this student from failing."""
    below = grade_of(Decimal("29.75"), bonus=Decimal("0.24"))
    exactly = grade_of(Decimal("29.75"), bonus=Decimal("0.25"))
    assert below.final_total == Decimal("29.99")
    assert below.grade == GRADE_FAILED
    assert exactly.final_total == Decimal("30.00")
    assert exactly.grade == "4.0"


@pytest.mark.parametrize(
    ("kwargs", "message_fragment"),
    [
        ({"exercise_points": [30.0]}, "exercise_points[0]"),
        ({"exercise_points": [Decimal(10), 20.0]}, "exercise_points[1]"),
        ({"bonus_points": 3.0}, "bonus_points"),
        ({"max_points": 60.0}, "max_points"),
        ({"thresholds": {**SCHEMA, "1.0": 95.0}}, "'1.0'"),
    ],
)
def test_float_arguments_are_rejected(kwargs: dict[str, object], message_fragment: str) -> None:
    """§7.0: a float is refused loudly, never silently converted."""
    call = {
        "exercise_points": [Decimal(30)],
        "bonus_points": Decimal(0),
        "attended": True,
        "bonus_mode": BONUS_MODE_ALWAYS,
        "thresholds": SCHEMA,
        "max_points": MAX_POINTS,
        **kwargs,
    }
    with pytest.raises(TypeError) as excinfo:
        compute_grade(**call)  # type: ignore[arg-type]
    assert message_fragment in str(excinfo.value)


def test_float_rejected_by_the_threshold_helpers_too() -> None:
    with pytest.raises(TypeError):
        point_thresholds(SCHEMA, 60.0)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        passing_threshold_points(SCHEMA, 60.0)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        grade_for_total(30.0, SCHEMA, MAX_POINTS)  # type: ignore[arg-type]


# --------------------------------------------------------------------------------------
# §7.3 — bonus modes
# --------------------------------------------------------------------------------------


def test_bonus_mode_only_if_passing_applies_at_exactly_the_passing_threshold() -> None:
    """The gate is ``raw_total >= threshold``, so a raw total of exactly 30.0 keeps its bonus."""
    result = grade_of(
        Decimal("30.0"), bonus=Decimal(3), mode=BONUS_MODE_ONLY_IF_PASSING_WITHOUT_BONUS
    )
    assert result.bonus_applied is True
    assert result.final_total == Decimal("33.0")
    assert result.grade == "3.7"


def test_bonus_mode_only_if_passing_withholds_one_hundredth_below_the_threshold() -> None:
    result = grade_of(
        Decimal("29.99"), bonus=Decimal(3), mode=BONUS_MODE_ONLY_IF_PASSING_WITHOUT_BONUS
    )
    assert result.bonus_applied is False
    assert result.final_total == Decimal("29.99")
    assert result.grade == GRADE_FAILED


@pytest.mark.parametrize("mode", BOTH_MODES)
def test_bonus_is_uncapped_and_may_exceed_max_points(mode: str) -> None:
    """§7.3: ``final_total`` may exceed ``max_points`` in both modes, and still yields 1.0."""
    result = grade_of(Decimal("56.0"), bonus=Decimal(10), mode=mode)
    assert result.final_total == Decimal("66.0")
    assert result.final_total > MAX_POINTS
    assert result.grade == "1.0"
    assert result.bonus_applied is True


@pytest.mark.parametrize("mode", BOTH_MODES)
def test_bonus_applied_is_true_for_a_zero_bonus_whose_gate_passed(mode: str) -> None:
    """``bonus_applied`` reports the §7.3 *rule*, not the magnitude (documented on GradeResult)."""
    result = grade_of(Decimal("30.0"), bonus=Decimal(0), mode=mode)
    assert result.bonus_applied is True
    assert result.final_total == Decimal("30.0")


def test_bonus_applied_is_false_when_the_rule_withholds_a_zero_bonus() -> None:
    """A failing student under ONLY_IF_… never has a bonus applied, even a zero one."""
    result = grade_of(
        Decimal("29.0"), bonus=Decimal(0), mode=BONUS_MODE_ONLY_IF_PASSING_WITHOUT_BONUS
    )
    assert result.bonus_applied is False
    assert result.final_total == Decimal("29.0")


def test_unknown_bonus_mode_raises_rather_than_defaulting() -> None:
    with pytest.raises(ValueError, match="Unknown bonus_mode"):
        grade_of(Decimal("30.0"), mode="ONLY_IF_PASSING")


def test_bonus_mode_constants_match_the_orm_enum() -> None:
    """The engine avoids importing the ORM, so pin the contract it relies on from the test side."""
    from app.models.exam import BonusMode

    assert BonusMode.ALWAYS == BONUS_MODE_ALWAYS
    assert BonusMode.ONLY_IF_PASSING_WITHOUT_BONUS == BONUS_MODE_ONLY_IF_PASSING_WITHOUT_BONUS
    # A BonusMode member is a str and can therefore be passed straight through.
    result = grade_of(Decimal("28.0"), bonus=Decimal(3), mode=BonusMode.ALWAYS)
    assert result.final_total == Decimal("31.0")
    assert result.grade == "4.0"


# --------------------------------------------------------------------------------------
# §7.4 / §4 — attendance
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("mode", BOTH_MODES)
def test_not_attended_overrides_even_a_perfect_score(mode: str) -> None:
    result = grade_of(Decimal("60.0"), bonus=Decimal(5), mode=mode, attended=False)
    assert result.grade == GRADE_NOT_ATTENDED
    assert result.status is GradeStatus.NOT_ATTENDED
    assert result.final_total is None
    assert result.bonus_applied is False
    assert result.is_passing is False
    # The entered points are still reported back untouched — only the grade is overridden.
    assert result.raw_total == Decimal("60.0")


def test_attendance_not_recorded_is_not_computable_and_not_n_e() -> None:
    """§4/§8.1: ``None`` means "not yet recorded" — never "n.e.", never a grade, never a zero."""
    result = grade_of(Decimal("55.0"), bonus=Decimal(3), attended=None)
    assert result.status is GradeStatus.ATTENDANCE_NOT_RECORDED
    assert result.grade is None
    assert result.grade != GRADE_NOT_ATTENDED
    assert result.final_total is None
    assert result.is_passing is False
    assert result.bonus_applied is False
    assert result.raw_total == Decimal("55.0")


def test_attendance_not_recorded_with_no_points_is_still_not_a_zero() -> None:
    """A student with nothing entered at all must not come out as "nicht bestanden"."""
    result = compute_grade(
        exercise_points=[],
        bonus_points=Decimal(0),
        attended=None,
        bonus_mode=BONUS_MODE_ALWAYS,
        thresholds=SCHEMA,
        max_points=MAX_POINTS,
    )
    assert result.status is GradeStatus.ATTENDANCE_NOT_RECORDED
    assert result.grade is None
    assert result.raw_total == Decimal(0)


def test_a_partially_entered_student_is_graded_provisionally_and_flagged_incomplete() -> None:
    """The engine grades what was entered; §8.1's gate — not the engine — is what blocks export.

    §8 needs a live grade while the instructor is still typing, so ``compute_grade`` sums only the
    entries it is given. That total is *provisional*: it is observationally identical to a student
    who genuinely scored those points on every exercise. The contract that keeps this safe is that
    :func:`check_completeness` must be consulted before any report, because an exercise with no
    ``ExercisePoints`` row must never be passed in as ``Decimal(0)``.
    """
    exercise_ids = (1, 2, 3)
    entered = {1: Decimal("20.0"), 3: Decimal("15.0")}  # exercise 2 not entered at all

    result = compute_grade(
        exercise_points=list(entered.values()),
        bonus_points=Decimal(0),
        attended=True,
        bonus_mode=BONUS_MODE_ALWAYS,
        thresholds=SCHEMA,
        max_points=MAX_POINTS,
    )
    # Computed from the two entered exercises only — exercise 2 contributes nothing, not a zero.
    assert result.raw_total == Decimal("35.0")
    assert result.grade == "3.7"
    assert result.status is GradeStatus.GRADED

    completeness = check_completeness(
        attended=True, exercise_ids=exercise_ids, entered_exercise_ids=entered.keys()
    )
    assert completeness.is_complete is False
    assert completeness.missing_exercise_ids == (2,)


def test_non_boolean_attended_is_rejected() -> None:
    with pytest.raises(TypeError, match="attended"):
        grade_of(Decimal("30.0"), attended="ja")  # type: ignore[arg-type]


# --------------------------------------------------------------------------------------
# Threshold-mapping preconditions
# --------------------------------------------------------------------------------------


def test_incomplete_threshold_mapping_raises() -> None:
    """A missing grade would silently award a worse grade than earned — so it must fail loudly."""
    incomplete = {grade: pct for grade, pct in SCHEMA.items() if grade != "2.0"}
    with pytest.raises(ValueError, match=r"2\.0"):
        grade_of(Decimal("48.0"), thresholds=incomplete)
    with pytest.raises(ValueError, match="missing"):
        point_thresholds(incomplete, MAX_POINTS)


def test_grade_for_total_returns_none_below_the_passing_threshold() -> None:
    assert grade_for_total(Decimal("29.99"), SCHEMA, MAX_POINTS) is None
    assert grade_for_total(Decimal("30.0"), SCHEMA, MAX_POINTS) == "4.0"
    assert grade_for_total(Decimal("999"), SCHEMA, MAX_POINTS) == "1.0"


def test_grade_result_is_immutable() -> None:
    result = grade_of(Decimal("30.0"))
    with pytest.raises(AttributeError):
        result.grade = "1.0"  # type: ignore[misc]


# --------------------------------------------------------------------------------------
# §8.1 — completeness gate
# --------------------------------------------------------------------------------------

EXERCISES = (1, 2, 3)


def test_completeness_attendance_not_recorded_is_incomplete() -> None:
    result = check_completeness(attended=None, exercise_ids=EXERCISES, entered_exercise_ids={1, 2})
    assert result == CompletenessResult(
        is_complete=False, attendance_missing=True, missing_exercise_ids=()
    )


def test_completeness_not_attended_needs_no_points() -> None:
    """§7.4/§8: a no-show is complete with nothing entered — don't demand zeros."""
    result = check_completeness(attended=False, exercise_ids=EXERCISES, entered_exercise_ids=set())
    assert result.is_complete is True
    assert result.attendance_missing is False
    assert result.missing_exercise_ids == ()


def test_completeness_attended_with_all_points_entered() -> None:
    result = check_completeness(
        attended=True, exercise_ids=EXERCISES, entered_exercise_ids={1, 2, 3}
    )
    assert result.is_complete is True
    assert result.missing_exercise_ids == ()


def test_completeness_attended_missing_points_lists_the_offenders() -> None:
    """§8.1 wants the specific incomplete rows, not just a boolean."""
    result = check_completeness(attended=True, exercise_ids=EXERCISES, entered_exercise_ids={2})
    assert result.is_complete is False
    assert result.attendance_missing is False
    assert result.missing_exercise_ids == (1, 3)


def test_completeness_an_entered_zero_counts_as_entered() -> None:
    """Absence of an ExercisePoints row means "not entered"; a row holding 0 does not (§8.1)."""
    entered = {exercise_id: Decimal(0) for exercise_id in EXERCISES}
    result = check_completeness(
        attended=True, exercise_ids=EXERCISES, entered_exercise_ids=entered.keys()
    )
    assert result.is_complete is True


def test_completeness_exam_without_exercises_is_complete() -> None:
    result = check_completeness(attended=True, exercise_ids=(), entered_exercise_ids=set())
    assert result.is_complete is True


def test_completeness_rejects_a_non_boolean_attendance() -> None:
    with pytest.raises(TypeError, match="attended"):
        check_completeness(attended=1, exercise_ids=EXERCISES, entered_exercise_ids=set())  # type: ignore[arg-type]
