"""Storage-layer tests: exact Decimal round-tripping, FK enforcement, cascades, constraints.

These guard SPECIFICATION.md §7.0 (no binary floats anywhere in the point path) and §13 (an
exam delete must take all of its personal data with it).
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import Engine, func, select, text
from sqlalchemy.exc import IntegrityError, StatementError
from sqlalchemy.orm import Session, sessionmaker

from app.models import Exam, Exercise, ExercisePoints, GradeThreshold, StudentRegistration
from app.types import DecimalText

ROUND_TRIP_VALUES = [
    Decimal("0.75"),
    Decimal("29.5"),
    Decimal("30.0"),
    Decimal(0),
    Decimal(100),
]


def _count(session: Session, model: type) -> int:
    return session.execute(select(func.count()).select_from(model)).scalar_one()


# --------------------------------------------------------------------------------------
# DecimalText unit level
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("value", ROUND_TRIP_VALUES)
def test_decimal_text_bind_preserves_exact_string(value: Decimal) -> None:
    assert DecimalText().process_bind_param(value, None) == str(value)


def test_decimal_text_result_returns_decimal() -> None:
    result = DecimalText().process_result_value("29.5", None)
    assert isinstance(result, Decimal)
    assert result == Decimal("29.5")


def test_decimal_text_accepts_int_and_str() -> None:
    assert DecimalText().process_bind_param(7, None) == "7"
    assert DecimalText().process_bind_param("0.75", None) == "0.75"


def test_decimal_text_passes_none_through() -> None:
    assert DecimalText().process_bind_param(None, None) is None
    assert DecimalText().process_result_value(None, None) is None


def test_decimal_text_rejects_float() -> None:
    """§7.0: a float reaching storage is the silent-corruption path — it must fail loudly."""
    with pytest.raises(TypeError):
        DecimalText().process_bind_param(0.75, None)


# --------------------------------------------------------------------------------------
# Round-tripping through a real SQLite file
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("value", ROUND_TRIP_VALUES)
def test_decimal_round_trip_through_sqlite(
    session: Session,
    session_factory: sessionmaker[Session],
    exam: Exam,
    value: Decimal,
) -> None:
    exercise = Exercise(exam_id=exam.id, name="Aufgabe X", max_points=value, position=99)
    session.add(exercise)
    registration = session.scalars(select(StudentRegistration)).one()
    session.flush()
    session.add(
        ExercisePoints(registration_id=registration.id, exercise_id=exercise.id, points=value)
    )
    session.commit()
    exercise_id = exercise.id

    # A brand-new session: values must come back from the database, not the identity map.
    with session_factory() as fresh:
        stored_exercise = fresh.get(Exercise, exercise_id)
        assert stored_exercise is not None
        stored_points = fresh.scalars(
            select(ExercisePoints).where(ExercisePoints.exercise_id == exercise_id)
        ).one()

        assert isinstance(stored_exercise.max_points, Decimal)
        assert isinstance(stored_points.points, Decimal)
        assert stored_exercise.max_points == value
        assert stored_points.points == value
        # Decimal("30.0") == Decimal("30") in Python, so also pin the exact digit string:
        # scale must survive storage, otherwise "30.0" silently becomes "30".
        assert str(stored_exercise.max_points) == str(value)
        assert str(stored_points.points) == str(value)


def test_decimal_scale_is_preserved_verbatim(
    session: Session, session_factory: sessionmaker[Session], exam: Exam
) -> None:
    session.add(GradeThreshold(exam_id=exam.id, grade="4.0", percentage=Decimal("50.00")))
    session.commit()

    with session_factory() as fresh:
        threshold = fresh.scalars(select(GradeThreshold)).one()
        assert str(threshold.percentage) == "50.00"
        assert threshold.percentage == Decimal(50)


def test_points_are_stored_as_text_not_real(session: Session, exam: Exam) -> None:
    """§7.0: the column must have TEXT affinity — REAL would be a binary float."""
    columns = session.execute(text("PRAGMA table_info(exercise_points)")).fetchall()
    types = {row[1]: row[2].upper() for row in columns}
    assert types["points"] == "TEXT"


def test_binding_a_float_through_the_orm_raises(session: Session, exam: Exam) -> None:
    registration = session.scalars(select(StudentRegistration)).one()
    exercise = session.scalars(select(Exercise)).one()
    session.add(
        ExercisePoints(
            registration_id=registration.id,
            exercise_id=exercise.id,
            points=0.75,  # type: ignore[arg-type]
        )
    )
    # SQLAlchemy wraps errors raised in process_bind_param during flush.
    with pytest.raises(StatementError) as excinfo:
        session.commit()
    assert isinstance(excinfo.value.orig, TypeError)
    session.rollback()


# --------------------------------------------------------------------------------------
# PRAGMAs
# --------------------------------------------------------------------------------------


def test_foreign_keys_pragma_is_on(session: Session) -> None:
    assert session.execute(text("PRAGMA foreign_keys")).scalar_one() == 1


def test_journal_mode_is_wal_for_file_databases(session: Session) -> None:
    assert session.execute(text("PRAGMA journal_mode")).scalar_one().lower() == "wal"


def test_foreign_keys_are_actually_enforced(session: Session, exam: Exam) -> None:
    registration = session.scalars(select(StudentRegistration)).one()
    session.add(
        ExercisePoints(
            registration_id=registration.id,
            exercise_id=999_999,  # no such exercise
            points=Decimal(1),
        )
    )
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()


# --------------------------------------------------------------------------------------
# §13 cascading exam delete
# --------------------------------------------------------------------------------------


def _populate(session: Session, exam: Exam) -> None:
    """Give the exam a second exercise, a threshold and points for its registration."""
    extra = Exercise(exam_id=exam.id, name="Aufgabe 2", max_points=Decimal(40), position=2)
    session.add(extra)
    session.add(GradeThreshold(exam_id=exam.id, grade="4.0", percentage=Decimal(50)))
    session.flush()
    registration = session.scalars(select(StudentRegistration)).one()
    for exercise in session.scalars(select(Exercise)).all():
        session.add(
            ExercisePoints(
                registration_id=registration.id,
                exercise_id=exercise.id,
                points=Decimal("12.5"),
            )
        )
    session.commit()


def _assert_everything_gone(session: Session) -> None:
    session.expire_all()
    assert _count(session, Exam) == 0
    assert _count(session, Exercise) == 0
    assert _count(session, GradeThreshold) == 0
    assert _count(session, StudentRegistration) == 0
    assert _count(session, ExercisePoints) == 0


def test_orm_delete_of_exam_cascades(session: Session, exam: Exam) -> None:
    """§13: session.delete(exam) removes every child row (ORM relationship cascade)."""
    _populate(session, exam)
    assert _count(session, ExercisePoints) == 2

    session.delete(exam)
    session.commit()

    _assert_everything_gone(session)


def test_database_level_cascade_of_exam_delete(
    session: Session, session_factory: sessionmaker[Session], exam: Exam
) -> None:
    """§13 again, but bypassing the ORM entirely.

    A raw ``DELETE FROM exams`` exercises only ``ON DELETE CASCADE`` plus
    ``PRAGMA foreign_keys=ON``. The ORM-cascade test above would still pass with foreign keys
    disabled, so this is the test that actually pins the database-level guarantee.
    """
    _populate(session, exam)
    exam_id = exam.id
    session.execute(text("DELETE FROM exams WHERE id = :id"), {"id": exam_id})
    session.commit()

    with session_factory() as fresh:
        assert _count(fresh, Exam) == 0
        assert _count(fresh, Exercise) == 0
        assert _count(fresh, GradeThreshold) == 0
        assert _count(fresh, StudentRegistration) == 0
        assert _count(fresh, ExercisePoints) == 0


def test_deleting_a_user_with_lectures_is_refused(session: Session, exam: Exam) -> None:
    """Lecture.owner_id is RESTRICT: exam data must never be destroyed by a user delete."""
    from app.models import User

    owner = session.scalars(select(User)).one()
    session.delete(owner)
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()


# --------------------------------------------------------------------------------------
# Constraints
# --------------------------------------------------------------------------------------


def test_duplicate_exercise_points_violates_unique_constraint(session: Session, exam: Exam) -> None:
    registration = session.scalars(select(StudentRegistration)).one()
    exercise = session.scalars(select(Exercise)).one()
    session.add(
        ExercisePoints(
            registration_id=registration.id, exercise_id=exercise.id, points=Decimal("1.5")
        )
    )
    session.commit()

    session.add(
        ExercisePoints(
            registration_id=registration.id, exercise_id=exercise.id, points=Decimal("2.0")
        )
    )
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()


def test_attendance_defaults_to_null_not_false(
    session: Session, session_factory: sessionmaker[Session], exam: Exam
) -> None:
    """§4/§8.1: 'not yet recorded' must stay distinguishable from 'nicht erschienen'."""
    with session_factory() as fresh:
        registration = fresh.scalars(select(StudentRegistration)).one()
        assert registration.attended is None
        assert registration.bonus_points == Decimal(0)


def test_engine_is_isolated_per_test(engine: Engine) -> None:
    """Sanity check that the fixture points at a tmp_path file, not the app's default DB."""
    assert engine.url.database is not None
    assert engine.url.database.endswith("gradinghelper_test.db")
