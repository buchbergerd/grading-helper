"""Tests for :mod:`app.statistics` (SPECIFICATION.md §9, §7.5).

Fixtures are built directly against the ORM and committed through the shared ``session`` fixture
(a real, file-backed SQLite database per ``tests/conftest.py``) — never through a request or a
query of this module's own, since :func:`build_exam_statistics` is a pure function of an
already-loaded ``Exam`` and must issue none itself (this module's own docstring). All names and
Matrikelnummern below are synthetic ("Muster", "Beispiel", plain digit strings) and never
resemble real students (CLAUDE.md).

§7.5's worked example is encoded first, verbatim, exactly as ``tests/test_grading_engine.py``
does — the acceptance-test spec for §7, now flowing all the way through into the §9 payload.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

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
from app.statistics import build_exam_statistics

#: §7.5's worked-example schema, identical to tests/test_grading_engine.py's SCHEMA: max_points =
#: 60, 1.0 at 95 %, 4.0 at 50 %, the eight grades in between spaced 5 % apart so every point
#: threshold is distinct.
WORKED_EXAMPLE_SCHEMA: dict[str, Decimal] = {
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


# --------------------------------------------------------------------------------------------
# Fixture helpers
# --------------------------------------------------------------------------------------------


def _make_exam(
    session: Session,
    owner: User,
    *,
    exercises: list[tuple[str, Decimal]] | None = None,
    grading_schema: dict[str, Decimal] | None = None,
    bonus_mode: BonusMode = BonusMode.ALWAYS,
) -> Exam:
    """A committed Exam with the given exercises and (optional) full grading schema."""
    lecture = Lecture(name="Beispielvorlesung", owner_id=owner.id)
    session.add(lecture)
    session.flush()
    exam = Exam(
        lecture_id=lecture.id,
        owner_id=owner.id,
        semester="WiSe 23/24",
        termin="1. Termin",
        exam_date=date(2024, 2, 12),
        bonus_mode=bonus_mode,
    )
    for position, (name, max_points) in enumerate(exercises or [], start=1):
        exam.exercises.append(Exercise(name=name, max_points=max_points, position=position))
    if grading_schema is not None:
        for grade, percentage in grading_schema.items():
            exam.grade_thresholds.append(GradeThreshold(grade=grade, percentage=percentage))
    session.add(exam)
    session.commit()
    return exam


def _registration(
    *,
    matrikelnummer: str,
    nachname: str = "Muster",
    vorname: str = "Erika",
    course_code: str = "B.Sc. Beispiel",
    versuch: int = 1,
    attended: bool | None,
    bonus_points: Decimal = Decimal(0),
    points: dict[int, Decimal] | None = None,
    excluded: bool = False,
) -> StudentRegistration:
    """One synthetic registration, not yet attached to an exam or session."""
    registration = StudentRegistration(
        matrikelnummer=matrikelnummer,
        nachname=nachname,
        vorname=vorname,
        course_code=course_code,
        module_title=f"Beispielvorlesung ({course_code})",
        versuch=versuch,
        attended=attended,
        bonus_points=bonus_points,
        excluded=excluded,
    )
    for exercise_id, value in (points or {}).items():
        registration.exercise_points.append(ExercisePoints(exercise_id=exercise_id, points=value))
    return registration


def _add(session: Session, exam: Exam, *registrations: StudentRegistration) -> None:
    exam.registrations.extend(registrations)
    session.add(exam)
    session.commit()


def _assert_no_float(value: object, path: str = "$") -> None:
    """Recursively fail if ``value`` contains a ``float`` anywhere (§7.0's payload-walk test)."""
    if isinstance(value, float):
        pytest.fail(f"float found at {path}: {value!r}")
    if isinstance(value, dict):
        for key, item in value.items():
            _assert_no_float(item, f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _assert_no_float(item, f"{path}[{index}]")


# --------------------------------------------------------------------------------------------
# §7.5 worked example, flowing into the distribution
# --------------------------------------------------------------------------------------------


def test_worked_example_always_bonus(session: Session, instructor_user: User) -> None:
    """Rows 1-4 of §7.5's table, ``bonus_mode=ALWAYS``."""
    exam = _make_exam(
        session,
        instructor_user,
        exercises=[("Aufgabe 1", Decimal(60))],
        grading_schema=WORKED_EXAMPLE_SCHEMA,
        bonus_mode=BonusMode.ALWAYS,
    )
    exercise_id = exam.exercises[0].id
    _add(
        session,
        exam,
        _registration(
            matrikelnummer="1", attended=True, points={exercise_id: Decimal("30.0")}
        ),  # -> 4.0
        _registration(
            matrikelnummer="2", attended=True, points={exercise_id: Decimal("29.5")}
        ),  # -> nicht bestanden
        _registration(
            matrikelnummer="3", attended=False, points={exercise_id: Decimal("29.5")}
        ),  # -> n.e., attendance overrides points
        _registration(
            matrikelnummer="4",
            attended=True,
            bonus_points=Decimal(3),
            points={exercise_id: Decimal("28.0")},
        ),  # -> final 31.0 -> 4.0
    )

    stats = build_exam_statistics(exam)

    assert stats["grading_configured"] is True
    assert stats["passing_threshold"] == "30.0"
    assert stats["counts"]["registered"] == 4
    assert stats["counts"]["attended"] == 3
    assert stats["counts"]["not_attended"] == 1
    assert stats["counts"]["attendance_not_recorded"] == 0
    assert stats["counts"]["incomplete"] == 0
    assert stats["counts"]["graded"] == 3
    assert stats["counts"]["passed"] == 2
    assert stats["counts"]["failed"] == 1

    dist = stats["grade_distribution"]
    numeric = {row["grade"]: row["count"] for row in dist["numeric"]}
    assert numeric["4.0"] == 2
    assert sum(numeric.values()) == 2
    assert dist["failed_count"] == 1
    assert dist["not_attended_count"] == 1
    assert dist["numeric_count"] == 2
    assert dist["mean"] == "4.00"
    assert dist["median"] == "4.00"

    assert stats["rates"]["attendance"] == {"numerator": 3, "denominator": 4, "percent": "75.0"}
    assert stats["rates"]["passing"] == {"numerator": 2, "denominator": 3, "percent": "66.7"}
    assert stats["rates"]["failure"] == {"numerator": 1, "denominator": 3, "percent": "33.3"}

    assert stats["total_points_histogram"]["included_count"] == 3


def test_worked_example_only_if_passing_without_bonus(
    session: Session, instructor_user: User
) -> None:
    """Rows 5-6 of §7.5's table, ``bonus_mode=ONLY_IF_PASSING_WITHOUT_BONUS``.

    A different exam from :func:`test_worked_example_always_bonus`: ``bonus_mode`` is a per-exam
    setting, so the two modes cannot be exercised on one exam's registrations.
    """
    exam = _make_exam(
        session,
        instructor_user,
        exercises=[("Aufgabe 1", Decimal(60))],
        grading_schema=WORKED_EXAMPLE_SCHEMA,
        bonus_mode=BonusMode.ONLY_IF_PASSING_WITHOUT_BONUS,
    )
    exercise_id = exam.exercises[0].id
    _add(
        session,
        exam,
        _registration(
            matrikelnummer="5",
            attended=True,
            bonus_points=Decimal(3),
            points={exercise_id: Decimal("28.0")},
        ),  # raw 28.0 < 30.0 -> bonus withheld -> nicht bestanden
        _registration(
            matrikelnummer="6",
            attended=True,
            bonus_points=Decimal(3),
            points={exercise_id: Decimal("32.0")},
        ),  # raw 32.0 >= 30.0 -> bonus applied -> final 35.0 -> 3.7
    )

    stats = build_exam_statistics(exam)

    assert stats["counts"]["failed"] == 1
    assert stats["counts"]["passed"] == 1
    numeric = {row["grade"]: row["count"] for row in stats["grade_distribution"]["numeric"]}
    assert numeric["3.7"] == 1
    assert sum(numeric.values()) == 1


# --------------------------------------------------------------------------------------------
# Histograms: range, boundaries, over-max values
# --------------------------------------------------------------------------------------------


def test_uncapped_bonus_extends_histogram_range_and_bins_the_student(
    session: Session, instructor_user: User
) -> None:
    """Regression: an ALWAYS-bonus final_total above max_points must not fall off the chart."""
    exam = _make_exam(
        session,
        instructor_user,
        exercises=[("Aufgabe 1", Decimal(10))],
        grading_schema=WORKED_EXAMPLE_SCHEMA,
        bonus_mode=BonusMode.ALWAYS,
    )
    exercise_id = exam.exercises[0].id
    _add(
        session,
        exam,
        _registration(
            matrikelnummer="1",
            attended=True,
            bonus_points=Decimal(5),
            points={exercise_id: Decimal("10")},
        ),
    )

    stats = build_exam_statistics(exam)
    hist = stats["total_points_histogram"]

    assert hist["reference_max"] == "10"
    assert hist["max_observed"] == "15"
    assert hist["included_count"] == 1
    assert hist["bins"][-1]["count"] == 1
    assert hist["bins"][-1]["upper"] == "15.0"


def test_over_max_exercise_entry_appears_in_its_own_histogram(
    session: Session, instructor_user: User
) -> None:
    """§8 warns about, but does not clamp, an over-max exercise entry; §9 must still show it."""
    exam = _make_exam(session, instructor_user, exercises=[("Aufgabe 1", Decimal(5))])
    exercise_id = exam.exercises[0].id
    _add(
        session,
        exam,
        _registration(matrikelnummer="1", attended=True, points={exercise_id: Decimal("7")}),
    )

    stats = build_exam_statistics(exam)
    hist = stats["exercise_histograms"][0]

    assert hist["reference_max"] == "5"
    assert hist["max_observed"] == "7"
    assert hist["included_count"] == 1


def test_quarter_point_exercise_entry_falls_in_the_expected_bin(
    session: Session, instructor_user: User
) -> None:
    """§7.0/§14 #4: 0.75 points is valid free-decimal entry; it must land in the 0.5-1.0 bin."""
    exam = _make_exam(session, instructor_user, exercises=[("Aufgabe 1", Decimal(10))])
    exercise_id = exam.exercises[0].id
    _add(
        session,
        exam,
        _registration(matrikelnummer="1", attended=True, points={exercise_id: Decimal("0.75")}),
    )

    stats = build_exam_statistics(exam)
    hist = stats["exercise_histograms"][0]
    target = next(b for b in hist["bins"] if b["lower"] == "0.5" and b["upper"] == "1.0")
    assert target["count"] == 1
    assert target["label"] == "0,5–1,0"  # noqa: RUF001 -- EN DASH is the label's data, not a typo


def test_boundary_value_opens_the_next_bin_and_max_closes_the_last_bin(
    session: Session, instructor_user: User
) -> None:
    """A value exactly on a bin edge opens that bin; the maximum lands in the closed last bin."""
    exam = _make_exam(session, instructor_user, exercises=[("Aufgabe 1", Decimal(3))])
    exercise_id = exam.exercises[0].id
    _add(
        session,
        exam,
        _registration(
            matrikelnummer="1", attended=True, points={exercise_id: Decimal("1.0")}
        ),  # exactly on the 0.5-1.0/1.0-1.5 boundary
        _registration(
            matrikelnummer="2", attended=True, points={exercise_id: Decimal("3.0")}
        ),  # the maximum
    )

    stats = build_exam_statistics(exam)
    hist = stats["exercise_histograms"][0]

    previous_bin = next(b for b in hist["bins"] if b["lower"] == "0.5" and b["upper"] == "1.0")
    boundary_bin = next(b for b in hist["bins"] if b["lower"] == "1.0" and b["upper"] == "1.5")
    assert previous_bin["count"] == 0
    assert boundary_bin["count"] == 1

    last_bin = hist["bins"][-1]
    assert last_bin["upper"] == "3.0"
    assert last_bin["count"] == 1


# --------------------------------------------------------------------------------------------
# Completeness / classification
# --------------------------------------------------------------------------------------------


def test_incomplete_student_excluded_from_grade_and_total_but_present_in_its_own_exercise(
    session: Session, instructor_user: User
) -> None:
    exam = _make_exam(
        session,
        instructor_user,
        exercises=[("Aufgabe 1", Decimal(30)), ("Aufgabe 2", Decimal(30))],
        grading_schema=WORKED_EXAMPLE_SCHEMA,
    )
    exercise_1_id = exam.exercises[0].id  # exercise 2's id is intentionally left unused/missing
    _add(
        session,
        exam,
        _registration(
            matrikelnummer="1", attended=True, points={exercise_1_id: Decimal("20.0")}
        ),  # exercise 2 missing
    )

    stats = build_exam_statistics(exam)

    assert stats["counts"]["incomplete"] == 1
    assert stats["counts"]["graded"] == 0
    assert stats["grade_distribution"]["numeric_count"] == 0
    assert stats["grade_distribution"]["failed_count"] == 0
    assert stats["total_points_histogram"]["included_count"] == 0

    exercise_1_hist, exercise_2_hist = stats["exercise_histograms"]
    assert exercise_1_hist["included_count"] == 1
    assert exercise_2_hist["included_count"] == 0


def test_attended_complete_with_no_schema_counts_only_toward_attended(
    session: Session, instructor_user: User
) -> None:
    """No schema -> nobody can be "graded"; "invent nothing" — not counted as incomplete either."""
    exam = _make_exam(session, instructor_user, exercises=[("Aufgabe 1", Decimal(10))])
    exercise_id = exam.exercises[0].id
    _add(
        session,
        exam,
        _registration(matrikelnummer="1", attended=True, points={exercise_id: Decimal("5")}),
    )

    stats = build_exam_statistics(exam)

    assert stats["grading_configured"] is False
    assert stats["counts"]["attended"] == 1
    assert stats["counts"]["graded"] == 0
    assert stats["counts"]["incomplete"] == 0
    assert stats["total_points_histogram"]["included_count"] == 0
    assert stats["exercise_histograms"][0]["included_count"] == 1


# --------------------------------------------------------------------------------------------
# Empty exams
# --------------------------------------------------------------------------------------------


def test_zero_registrations_returns_valid_payload(session: Session, instructor_user: User) -> None:
    """The histogram range still extends to a nonzero ``max_points`` with nobody registered."""
    exam = _make_exam(
        session,
        instructor_user,
        exercises=[("Aufgabe 1", Decimal(10))],
        grading_schema=WORKED_EXAMPLE_SCHEMA,
    )

    stats = build_exam_statistics(exam)

    assert stats["counts"]["registered"] == 0
    hist = stats["total_points_histogram"]
    assert hist["included_count"] == 0
    assert hist["max_observed"] is None
    assert len(hist["bins"]) == 10
    assert all(b["count"] == 0 for b in hist["bins"])
    assert stats["grade_distribution"]["mean"] is None
    assert stats["versuch_breakdown"] == []


def test_no_exercises_and_no_schema_returns_valid_payload(
    session: Session, instructor_user: User
) -> None:
    """The one documented empty-histogram case: zero contributors *and* reference_max of 0."""
    exam = _make_exam(session, instructor_user)

    stats = build_exam_statistics(exam)

    assert stats["grading_configured"] is False
    assert stats["max_points"] == "0"
    assert stats["passing_threshold"] is None
    assert stats["exercise_histograms"] == []
    assert stats["total_points_histogram"] == {
        "title": "Gesamtpunkte",
        "bin_width": "1.0",
        "reference_max": "0",
        "max_observed": None,
        "included_count": 0,
        "bins": [],
    }
    assert stats["counts"]["registered"] == 0


# --------------------------------------------------------------------------------------------
# Mean / median
# --------------------------------------------------------------------------------------------


def test_median_of_even_numeric_count_is_exact_mean_before_rounding(
    session: Session, instructor_user: User
) -> None:
    exam = _make_exam(
        session,
        instructor_user,
        exercises=[("Aufgabe 1", Decimal(60))],
        grading_schema=WORKED_EXAMPLE_SCHEMA,
    )
    exercise_id = exam.exercises[0].id
    _add(
        session,
        exam,
        _registration(
            matrikelnummer="1", attended=True, points={exercise_id: Decimal("57.0")}
        ),  # 1.0
        _registration(
            matrikelnummer="2", attended=True, points={exercise_id: Decimal("54.0")}
        ),  # 1.3
        _registration(
            matrikelnummer="3", attended=True, points={exercise_id: Decimal("42.0")}
        ),  # 2.7
        _registration(
            matrikelnummer="4", attended=True, points={exercise_id: Decimal("30.0")}
        ),  # 4.0
    )

    stats = build_exam_statistics(exam)
    dist = stats["grade_distribution"]

    # sorted numeric grades: 1.0, 1.3, 2.7, 4.0
    # -> median = (1.3 + 2.7) / 2 = 2.0, mean = 9.0 / 4 = 2.25
    assert dist["numeric_count"] == 4
    assert dist["median"] == "2.00"
    assert dist["mean"] == "2.25"


def test_mean_and_median_are_none_when_nobody_has_a_numeric_grade(
    session: Session, instructor_user: User
) -> None:
    exam = _make_exam(
        session,
        instructor_user,
        exercises=[("Aufgabe 1", Decimal(60))],
        grading_schema=WORKED_EXAMPLE_SCHEMA,
    )
    exercise_id = exam.exercises[0].id
    _add(
        session,
        exam,
        _registration(
            matrikelnummer="1", attended=True, points={exercise_id: Decimal("10.0")}
        ),  # nicht bestanden
        _registration(matrikelnummer="2", attended=False),  # n.e.
    )

    stats = build_exam_statistics(exam)
    dist = stats["grade_distribution"]

    assert dist["numeric_count"] == 0
    assert dist["mean"] is None
    assert dist["median"] is None


# --------------------------------------------------------------------------------------------
# Versuch breakdown
# --------------------------------------------------------------------------------------------


def test_sparse_versuch_values_produce_exactly_two_ascending_groups(
    session: Session, instructor_user: User
) -> None:
    exam = _make_exam(session, instructor_user, exercises=[("Aufgabe 1", Decimal(10))])
    exercise_id = exam.exercises[0].id
    _add(
        session,
        exam,
        _registration(
            matrikelnummer="1", versuch=4, attended=True, points={exercise_id: Decimal("5")}
        ),
        _registration(
            matrikelnummer="2", versuch=1, attended=True, points={exercise_id: Decimal("5")}
        ),
        _registration(matrikelnummer="3", versuch=1, attended=False),
    )

    stats = build_exam_statistics(exam)
    groups = stats["versuch_breakdown"]

    assert [g["versuch"] for g in groups] == [1, 4]
    assert groups[0]["label"] == "1. Versuch"
    assert groups[0]["registered"] == 2
    assert groups[1]["label"] == "4. Versuch"
    assert groups[1]["registered"] == 1


# --------------------------------------------------------------------------------------------
# Excluded students
# --------------------------------------------------------------------------------------------


def test_excluded_student_affects_only_the_excluded_count(
    session: Session, instructor_user: User
) -> None:
    exam = _make_exam(
        session,
        instructor_user,
        exercises=[("Aufgabe 1", Decimal(10))],
        grading_schema=WORKED_EXAMPLE_SCHEMA,
    )
    exercise_id = exam.exercises[0].id
    _add(
        session,
        exam,
        _registration(matrikelnummer="1", attended=True, points={exercise_id: Decimal("10")}),
        _registration(
            matrikelnummer="2",
            attended=True,
            points={exercise_id: Decimal("999")},
            excluded=True,
        ),
    )

    stats = build_exam_statistics(exam)

    assert stats["counts"]["excluded"] == 1
    assert stats["counts"]["registered"] == 1
    assert stats["total_points_histogram"]["included_count"] == 1
    assert stats["exercise_histograms"][0]["included_count"] == 1
    assert stats["exercise_histograms"][0]["max_observed"] == "10"


# --------------------------------------------------------------------------------------------
# Dates, JSON-serialisability, no-float
# --------------------------------------------------------------------------------------------


def test_generated_at_and_exam_date_use_german_date_format(
    session: Session, instructor_user: User
) -> None:
    exam = _make_exam(session, instructor_user)
    stats = build_exam_statistics(exam, now=datetime(2026, 7, 28, 14, 5))
    assert stats["generated_at"] == "28.07.2026 14:05"
    assert stats["exam_date"] == "12.02.2024"


def test_payload_contains_no_float_anywhere(session: Session, instructor_user: User) -> None:
    exam = _make_exam(
        session,
        instructor_user,
        exercises=[("Aufgabe 1", Decimal(60)), ("Aufgabe 2", Decimal(40))],
        grading_schema=WORKED_EXAMPLE_SCHEMA,
    )
    exercise_1_id, exercise_2_id = exam.exercises[0].id, exam.exercises[1].id
    _add(
        session,
        exam,
        _registration(
            matrikelnummer="1",
            attended=True,
            bonus_points=Decimal(3),
            points={exercise_1_id: Decimal("57.0"), exercise_2_id: Decimal("0.75")},
        ),
        _registration(
            matrikelnummer="2", attended=True, points={exercise_1_id: Decimal("29.5")}
        ),
        _registration(matrikelnummer="3", attended=False),
        _registration(matrikelnummer="4", attended=None),
    )

    stats = build_exam_statistics(exam)
    _assert_no_float(stats)


def test_payload_is_json_serialisable(session: Session, instructor_user: User) -> None:
    exam = _make_exam(
        session,
        instructor_user,
        exercises=[("Aufgabe 1", Decimal(60))],
        grading_schema=WORKED_EXAMPLE_SCHEMA,
    )
    exercise_id = exam.exercises[0].id
    _add(
        session,
        exam,
        _registration(matrikelnummer="1", attended=True, points={exercise_id: Decimal("57.0")}),
    )

    stats = build_exam_statistics(exam)
    json.dumps(stats)  # must not raise
