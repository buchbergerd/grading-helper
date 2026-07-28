"""Custom SQLAlchemy column types.

SPECIFICATION.md §7.0: *all* point/percentage/threshold values must be exact decimals end to
end. SQLite's ``REAL`` affinity is an IEEE-754 binary float, and SQLAlchemy's ``Numeric`` type
round-trips through ``float`` on SQLite — so **neither ``Numeric`` nor ``Float`` nor a bare
``REAL`` column may be used anywhere in this codebase**. Every decimal-valued column uses
:class:`DecimalText`, which stores the value as its exact decimal string in a ``TEXT`` column.

Consequence you must keep in mind everywhere else:

    Because point values are stored as TEXT, they do **not** sort or compare numerically in SQL.
    ``"10.0" < "9.0"`` is true as a string comparison. Any ordering, filtering, aggregation or
    min/max over point or percentage values must therefore be done **in Python** on the decoded
    ``Decimal`` objects — never in an SQL ``ORDER BY``, ``WHERE`` or ``SUM()``.

Ordering by non-decimal columns (``Exercise.position``, ``matrikelnummer``, ids) is unaffected.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from sqlalchemy import Dialect, Text
from sqlalchemy.types import TypeDecorator


class DecimalText(TypeDecorator[Decimal]):
    """Store a :class:`~decimal.Decimal` losslessly as its decimal string in a TEXT column."""

    impl = Text
    cache_ok = True

    def process_bind_param(self, value: Any, dialect: Dialect) -> str | None:
        if value is None:
            return None
        # Check for float *before* constructing a Decimal: Decimal(0.75) succeeds and silently
        # yields the binary expansion 0.74999999999999988897769753748...  That is exactly the
        # silent-corruption path §7.0 warns about, so refuse it loudly instead.
        if isinstance(value, float):
            raise TypeError(
                "DecimalText refuses float values (§7.0: exact decimal arithmetic only). "
                f"Got {value!r}; pass a Decimal, int or str instead."
            )
        if isinstance(value, Decimal):
            return str(value)
        if isinstance(value, (int, str)):
            return str(Decimal(value))
        raise TypeError(
            f"DecimalText cannot bind value of type {type(value).__name__!r}: {value!r}"
        )

    def process_result_value(self, value: Any, dialect: Dialect) -> Decimal | None:
        if value is None:
            return None
        return Decimal(value)
