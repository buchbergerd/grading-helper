"""Tests for :mod:`app.formatting` (SPECIFICATION.md §14 #6, §7.0)."""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.formatting import format_german_decimal


def test_keeps_own_scale_when_places_is_none() -> None:
    assert format_german_decimal(Decimal("57.0")) == "57,0"
    assert format_german_decimal(Decimal("60")) == "60"
    assert format_german_decimal(Decimal("1.3")) == "1,3"


def test_quantizes_to_requested_places() -> None:
    assert format_german_decimal(Decimal("29.5"), places=2) == "29,50"
    assert format_german_decimal(Decimal("29.567"), places=1) == "29,6"


def test_round_half_up_not_banker_rounding() -> None:
    """2.25 is an exact tie at the second place; ROUND_HALF_UP breaks it upward, not to even."""
    assert format_german_decimal(Decimal("2.25"), places=1) == "2,3"


def test_round_half_up_negative_rounds_away_from_zero() -> None:
    assert format_german_decimal(Decimal("-1.25"), places=1) == "-1,3"


def test_no_thousands_separator() -> None:
    assert format_german_decimal(Decimal("12345.5")) == "12345,5"


def test_never_touches_float() -> None:
    """Decimal("0.1") + Decimal("0.2") is exactly Decimal("0.3") — a float path would corrupt it."""
    value = Decimal("0.1") + Decimal("0.2")
    assert format_german_decimal(value) == "0,3"


def test_rejects_float_input() -> None:
    with pytest.raises(TypeError):
        format_german_decimal(0.75)  # type: ignore[arg-type]


def test_places_zero_quantizes_to_integer() -> None:
    assert format_german_decimal(Decimal("4.6"), places=0) == "5"
