"""Grading schema: the grade scale, its validation, and threshold computation (§7.1, §7.2).

Pure functions over :class:`~decimal.Decimal` — no database, no FastAPI, no floats. Full grade
*assignment* (bonus modes per §7.3, attendance per §7.4) is a later milestone and deliberately
not implemented here.

Error messages returned by :func:`validate_grading_schema` are German: they are shown verbatim
in the UI (CLAUDE.md: everything user-facing is German).
"""

from __future__ import annotations

from collections.abc import Mapping
from decimal import ROUND_FLOOR, Decimal
from itertools import pairwise

#: The ten passing grades of the German university scale, best to worst (§7.1). Strings, never
#: floats: these are labels, and 1.3 has no exact binary representation.
GRADES: tuple[str, ...] = (
    "1.0",
    "1.3",
    "1.7",
    "2.0",
    "2.3",
    "2.7",
    "3.0",
    "3.3",
    "3.7",
    "4.0",
)

#: The worst still-passing grade. Below its threshold a student has failed (§7.2, §7.4).
PASSING_GRADE = "4.0"

_HALF = Decimal("0.5")
_HUNDRED = Decimal(100)


def _format(value: Decimal) -> str:
    """Format a percentage for a German-language error message (comma decimal separator)."""
    return f"{value:f}".replace(".", ",")


def validate_grading_schema(percentages: Mapping[str, Decimal]) -> list[str]:
    """Validate a per-exam grading schema; return German error messages (empty list = valid).

    Checks, per §7.2:

    * all ten grades of :data:`GRADES` are present, and nothing else is;
    * every percentage lies in ``(0, 100]``;
    * percentages are **strictly decreasing** from "1.0" down to "4.0" — each better grade must
      require a strictly higher percentage than the next worse one.

    Raises :class:`TypeError` if any value is a ``float``: per §7.0 a float in this path is a
    programming error, not user input to be reported back politely.
    """
    errors: list[str] = []

    for grade, value in percentages.items():
        if isinstance(value, float):
            raise TypeError(
                f"Grading schema percentage for grade {grade!r} is a float ({value!r}); "
                "§7.0 requires exact Decimal arithmetic."
            )

    missing = [grade for grade in GRADES if grade not in percentages]
    if missing:
        errors.append("Fehlende Noten im Notenschema: " + ", ".join(missing) + ".")

    extra = [grade for grade in percentages if grade not in GRADES]
    if extra:
        errors.append("Unbekannte Noten im Notenschema: " + ", ".join(sorted(extra)) + ".")

    for grade in GRADES:
        if grade not in percentages:
            continue
        value = percentages[grade]
        if value <= 0 or value > _HUNDRED:
            errors.append(
                f"Prozentwert für Note {grade} muss größer als 0 und höchstens 100 sein "
                f"(aktuell: {_format(value)} %)."
            )

    for better, worse in pairwise(GRADES):
        if better not in percentages or worse not in percentages:
            continue
        if percentages[better] <= percentages[worse]:
            errors.append(
                f"Prozentwerte müssen von 1.0 bis 4.0 streng fallend sein: Note {better} "
                f"({_format(percentages[better])} %) muss einen höheren Prozentwert haben als "
                f"Note {worse} ({_format(percentages[worse])} %)."
            )

    return errors


def threshold_points(percentage: Decimal, max_points: Decimal) -> Decimal:
    """Point threshold for a grade, per §7.2.

    ``floor((percentage / 100 * max_points) / 0.5) * 0.5`` — the raw percentage-of-max-points
    cutoff, rounded **down** to the nearest 0.5 points. Exact decimal arithmetic throughout;
    ``math.floor`` on a float would reintroduce exactly the error §7.0 forbids.

    §7.5 worked example (``max_points = 60``): 95 % → ``Decimal("57.0")``,
    50 % → ``Decimal("30.0")``.
    """
    if isinstance(percentage, float) or isinstance(max_points, float):
        raise TypeError("threshold_points requires Decimal arguments, not float (§7.0).")
    raw = percentage / _HUNDRED * max_points
    steps = (raw / _HALF).to_integral_value(rounding=ROUND_FLOOR)
    return steps * _HALF
