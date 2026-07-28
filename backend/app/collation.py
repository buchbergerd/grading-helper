"""German (DIN 5007-1) name collation for the printed attendance list.

SPECIFICATION.md §6: the printed attendance list is sorted by course, then by last name within
course, using German (DIN 5007-1) collation — the "dictionary" variant, where umlauts sort as
their base letter (ö → o, ü → u, ä → a) and ß sorts as "ss". A plain Python ``sorted()`` on the
raw strings instead does a codepoint (byte-order) sort, which puts every name starting with an
umlaut *after* "Z" (because Ö is codepoint U+00D6, well past "Z" at U+005A) — an instructor
scanning a printed, alphabetically-sorted sheet by eye would find that visibly, embarrassingly
wrong: "Öztürk" stranded at the very end of the list instead of sitting among the "O" names.

This is deliberately DIN 5007-**1**, not DIN 5007-2. The two standards disagree on exactly this
point and mixing them up is the classic way to get German sorting wrong:

- DIN 5007-1 ("Variante 1", the phonebook/dictionary variant used for personal names and this
  attendance list): ä/ö/ü sort as a/o/u, ß sorts as "ss". This is what this module implements.
- DIN 5007-2 ("Variante 2", used for dictionary headwords): ä/ö/ü sort as "ae"/"oe"/"ue". If this
  module ever used that expansion, "Müller" would sort after "Mueller" as "mueller" too — which
  is DIN 5007-2 behaviour, not what §6 asks for. Do not "fix" this module to expand umlauts to
  two-letter digraphs; that is a different, deliberately-not-chosen standard.

No PyICU/libicu and no new dependency (pyuca's DUCET table is a large dependency for what is a
small, well-defined transformation) — see SPECIFICATION.md §12/§13 on keeping the Docker image
free of heavy system dependencies and working fully offline. The sort key is built directly with
``unicodedata`` from the standard library.

Names are sorted exactly as printed in the source PDF — no attempt is made here to detect or
reorder nobiliary particles ("von", "van", ...); "von Arendelle" sorts under "v", as given.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Callable, Iterable

# Characters that do not decompose (via NFD) into a base letter plus combining marks, so they
# must be expanded by hand before NFD normalization runs. Includes the German ß/ẞ (DIN 5007-1:
# ß ≍ ss) and a handful of other Latin letters an international student cohort's names will
# plausibly contain (Scandinavian, Polish, Icelandic, Turkish dotless i, ...).
_PRE_NFD_EXPANSIONS: dict[str, str] = {
    "ß": "ss",
    "ẞ": "SS",
    "æ": "ae",
    "Æ": "AE",
    "œ": "oe",
    "Œ": "OE",
    "ø": "o",
    "Ø": "O",
    "đ": "d",
    "Đ": "D",
    "ð": "d",
    "Ð": "D",
    "ł": "l",
    "Ł": "L",
    "þ": "th",
    "Þ": "Th",
    "ħ": "h",
    "Ħ": "H",
    "ı": "i",  # noqa: RUF001 -- Turkish dotless i, not a typo for "i"
}


def _expand(value: str) -> str:
    return "".join(_PRE_NFD_EXPANSIONS.get(char, char) for char in value)


def german_sort_key(value: str) -> tuple[str, str]:
    """Sort key implementing DIN 5007-1 ("Variante 1") collation for a single string.

    Umlauts and accents fold to their base letter (ö → o, é → e, å → a); ß folds to "ss".
    Case is ignored. The returned tuple's second element is the original (unmodified) string,
    used purely as a deterministic tiebreaker so that names differing only in diacritics (e.g.
    "Muller" vs "Müller") don't compare equal and fall back on whatever order the input
    happened to be in.
    """
    expanded = _expand(value)
    decomposed = unicodedata.normalize("NFD", expanded)
    stripped = "".join(char for char in decomposed if unicodedata.category(char) != "Mn")
    primary = stripped.casefold()
    return (primary, value)


def german_sorted[T](values: Iterable[T], key: Callable[[T], str]) -> list[T]:
    """Sort arbitrary objects by a string attribute, using German DIN 5007-1 collation."""
    return sorted(values, key=lambda item: german_sort_key(key(item)))
