"""German-locale decimal formatting, shared across statistics and future reports (§14 #6).

``format_german_date`` (``DD.MM.YYYY``) is deliberately **not** moved here in this milestone: it
still lives in ``app.reports.attendance_list``, where §6 introduced it first. Moving it would be
an unrelated mechanical change to a module this milestone otherwise doesn't touch, and
``app/statistics.py`` — the one new caller that also needs a ``DD.MM.YYYY`` formatter — copies the
two-line implementation instead of importing across the core→reports boundary (see its own
comment for why). Worth consolidating both formatters here later; not done now to keep this
change scoped to what §9 actually needs.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal


def format_german_decimal(value: Decimal, *, places: int | None = None) -> str:
    """Format ``value`` with a comma decimal separator, never touching binary float (§7.0, §14 #6).

    ``places=None`` (the default) keeps the value's own stored scale — ``Decimal("57.0")`` becomes
    ``"57,0"``, ``Decimal("60")`` becomes ``"60"``. Passing an ``int`` quantizes to exactly that
    many decimal places first, with ``ROUND_HALF_UP`` — the same rounding rule §9 uses for every
    percentage and mean/median. No thousands separator is ever produced; nothing this app formats
    is large enough to need one.

    Refuses a ``float`` input the same way :mod:`app.grading.engine` does: silently accepting one
    would let ``Decimal(0.75)``'s binary expansion leak into user-facing text, which is exactly
    the corruption §7.0 exists to prevent.
    """
    if isinstance(value, float):
        raise TypeError(
            f"format_german_decimal refuses float ({value!r}); §7.0 requires exact Decimal "
            "arithmetic. Pass a Decimal instead."
        )
    quantized = value
    if places is not None:
        quantum = Decimal(1).scaleb(-places)
        quantized = value.quantize(quantum, rounding=ROUND_HALF_UP)
    return f"{quantized:f}".replace(".", ",")
