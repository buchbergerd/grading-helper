"""Tests for the grading schema and threshold rounding (SPECIFICATION.md §7.1, §7.2, §7.5)."""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.grading.schema import GRADES, PASSING_GRADE, threshold_points, validate_grading_schema

# A valid, strictly decreasing schema covering all ten grades.
VALID_SCHEMA = {
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


def test_grade_scale_matches_spec() -> None:
    assert GRADES == ("1.0", "1.3", "1.7", "2.0", "2.3", "2.7", "3.0", "3.3", "3.7", "4.0")
    assert PASSING_GRADE == "4.0"
    assert GRADES[-1] == PASSING_GRADE


# --------------------------------------------------------------------------------------
# §7.5 worked example
# --------------------------------------------------------------------------------------


def test_threshold_points_worked_example_best_grade() -> None:
    """§7.5: 95 % of 60 points → 57.0."""
    result = threshold_points(Decimal(95), Decimal(60))
    assert result == Decimal("57.0")
    assert str(result) == "57.0"
    assert isinstance(result, Decimal)


def test_threshold_points_worked_example_passing_grade() -> None:
    """§7.5: 50 % of 60 points → 30.0."""
    result = threshold_points(Decimal(50), Decimal(60))
    assert result == Decimal("30.0")
    assert str(result) == "30.0"


@pytest.mark.parametrize(
    ("percentage", "max_points", "expected"),
    [
        # Rounds *down* to the nearest 0.5, never up.
        (Decimal(60), Decimal(45), Decimal("27.0")),  # 27.000000000000004 as a float
        (Decimal(50), Decimal(61), Decimal("30.5")),  # 30.5 exactly on a step
        (Decimal(51), Decimal(61), Decimal("31.0")),  # 31.11 → 31.0
        (Decimal(99), Decimal(60), Decimal("59.0")),  # 59.4 → 59.0
        (Decimal(100), Decimal(60), Decimal("60.0")),
        (Decimal(1), Decimal(60), Decimal("0.5")),  # 0.6 → 0.5
        (Decimal("0.5"), Decimal(60), Decimal("0.0")),  # 0.3 → 0.0, no negative zero games
        (Decimal("33.33"), Decimal(60), Decimal("19.5")),  # 19.998 → 19.5
    ],
)
def test_threshold_points_rounds_down_to_half_points(
    percentage: Decimal, max_points: Decimal, expected: Decimal
) -> None:
    assert threshold_points(percentage, max_points) == expected


def test_threshold_points_rejects_float() -> None:
    with pytest.raises(TypeError):
        threshold_points(95.0, Decimal(60))  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        threshold_points(Decimal(95), 60.0)  # type: ignore[arg-type]


# --------------------------------------------------------------------------------------
# Schema validation (§7.2)
# --------------------------------------------------------------------------------------


def test_valid_schema_produces_no_errors() -> None:
    assert validate_grading_schema(VALID_SCHEMA) == []


def test_valid_schema_with_decimal_percentages() -> None:
    schema = dict(VALID_SCHEMA)
    schema["1.0"] = Decimal("95.5")
    schema["4.0"] = Decimal("49.75")
    assert validate_grading_schema(schema) == []


def test_missing_grade_is_reported() -> None:
    schema = dict(VALID_SCHEMA)
    del schema["2.7"]
    errors = validate_grading_schema(schema)
    assert errors
    assert any("2.7" in error and "Fehlende" in error for error in errors)


def test_unknown_grade_is_reported() -> None:
    schema = dict(VALID_SCHEMA)
    schema["5.0"] = Decimal(10)
    errors = validate_grading_schema(schema)
    assert any("Unbekannte" in error and "5.0" in error for error in errors)


@pytest.mark.parametrize("bad_value", [Decimal(0), Decimal(-5), Decimal("100.01")])
def test_out_of_range_percentage_is_reported(bad_value: Decimal) -> None:
    schema = dict(VALID_SCHEMA)
    schema["4.0"] = bad_value
    errors = validate_grading_schema(schema)
    assert any("Prozentwert für Note 4.0" in error for error in errors)


def test_percentage_of_exactly_100_is_allowed() -> None:
    schema = dict(VALID_SCHEMA)
    schema["1.0"] = Decimal(100)
    assert validate_grading_schema(schema) == []


def test_non_strictly_decreasing_schema_is_rejected() -> None:
    """A better grade requiring a *lower* percentage than the next worse one is invalid."""
    schema = dict(VALID_SCHEMA)
    schema["2.0"] = Decimal(70)  # below 2.3's 75
    errors = validate_grading_schema(schema)
    assert any("streng fallend" in error for error in errors)


def test_equal_percentages_are_rejected() -> None:
    """'Strictly' decreasing: two grades may not share a percentage."""
    schema = dict(VALID_SCHEMA)
    schema["3.3"] = schema["3.7"]
    errors = validate_grading_schema(schema)
    assert any("streng fallend" in error for error in errors)


def test_validation_rejects_float_percentages_loudly() -> None:
    schema: dict[str, Decimal] = dict(VALID_SCHEMA)
    schema["1.0"] = 95.0  # type: ignore[assignment]
    with pytest.raises(TypeError):
        validate_grading_schema(schema)
