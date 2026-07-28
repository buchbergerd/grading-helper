"""Tests for German (DIN 5007-1) collation, used to sort the printed attendance list (§6).

This is printed output a human scans by eye — a sort bug here is not an abstract test failure,
it is a name stranded in the wrong place on a piece of paper an instructor is physically ticking
names off of. Tests are correspondingly picky about exact resulting order, not just relative
pairs.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from app.collation import german_sort_key, german_sorted

# --------------------------------------------------------------------------------------
# The spec's own example
# --------------------------------------------------------------------------------------


def test_oeztuerk_sorts_under_o_not_after_z() -> None:
    names = ["Zimmermann", "Öztürk", "Obermeier", "Ostermann"]
    assert sorted(names, key=german_sort_key) == [
        "Obermeier",
        "Ostermann",
        "Öztürk",
        "Zimmermann",
    ]


# --------------------------------------------------------------------------------------
# ß ≍ ss
# --------------------------------------------------------------------------------------


def test_sharp_s_folds_to_ss_and_sorts_adjacent_to_ss_spelling() -> None:
    names = ["Straßer", "Strasser", "Straßburg"]
    # Straßburg's primary key is "strassburg", which sorts before "strasser" (b < e at the
    # first differing position) — i.e. before *both* the Strasser/Straßer pair, which are then
    # ordered relative to each other only by the literal-string tiebreaker ('s' < 'ß').
    assert sorted(names, key=german_sort_key) == ["Straßburg", "Strasser", "Straßer"]


def test_weiss_sorts_with_weiss_not_after_weizen() -> None:
    names = ["Weizen", "Weiß", "Weiss"]
    result = sorted(names, key=german_sort_key)
    # "weiss"/"weiß" both fold to primary key "weiss", which precedes "weizen" ('s' < 'z');
    # Weiss sorts before Weiß only via the literal tiebreaker.
    assert result == ["Weiss", "Weiß", "Weizen"]


# --------------------------------------------------------------------------------------
# Umlauts
# --------------------------------------------------------------------------------------


def test_umlaut_folds_to_base_letter_muller_family() -> None:
    names = ["Mustermann", "Müller", "Mueller", "Muller"]
    # Primary keys: "Mueller" -> "mueller", "Müller" -> "muller", "Muller" -> "muller",
    # "Mustermann" -> "mustermann". Comparing character by character after "mu":
    #   "mueller" has 'e' in the third position, "muller"/"mustermann" have 'l'/'s' there.
    #   'e' < 'l' < 's', so "mueller" sorts BEFORE the "muller" spellings, which in turn sort
    #   before "mustermann". This is DIN 5007-1 (umlaut -> base letter): under DIN 5007-2
    #   (umlaut -> digraph) "Müller" would instead become "mueller" and tie with "Mueller" —
    #   that is deliberately not what this module implements, see the module docstring.
    # Müller and Muller share the primary key "muller"; the literal-string tiebreaker then
    # orders them, since 'l' (U+006C) < 'ü' (U+00FC) at the first differing character.
    assert sorted(names, key=german_sort_key) == [
        "Mueller",
        "Muller",
        "Müller",
        "Mustermann",
    ]


# --------------------------------------------------------------------------------------
# Accented non-German names (international student cohort)
# --------------------------------------------------------------------------------------


def test_accented_names_sort_under_their_base_letter() -> None:
    cases = [
        ("Évora", "e"),
        ("Ångström", "a"),
        ("Łukasiewicz", "l"),
        ("Škoda", "s"),
        ("Ćwiek", "c"),
    ]
    for name, expected_first_letter in cases:
        primary, _ = german_sort_key(name)
        assert primary.startswith(expected_first_letter), (name, primary)

    names = [name for name, _ in cases]
    assert sorted(names, key=german_sort_key) == [
        "Ångström",
        "Ćwiek",
        "Évora",
        "Łukasiewicz",
        "Škoda",
    ]


# --------------------------------------------------------------------------------------
# Pre-NFD expansion table: characters that do not decompose into a base letter via NFD,
# so must be expanded by hand. Each of these is untested by the other cases above.
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("char", "expected_primary"),
    [
        ("ß", "ss"),
        ("ẞ", "ss"),  # capital sharp s (U+1E9E) -- distinct codepoint from lowercase ß
        ("æ", "ae"),
        ("Æ", "ae"),
        ("œ", "oe"),
        ("Œ", "oe"),
        ("ø", "o"),
        ("Ø", "o"),
        ("đ", "d"),
        ("Đ", "d"),
        ("ð", "d"),
        ("Ð", "d"),
        ("ł", "l"),
        ("Ł", "l"),
        ("þ", "th"),
        ("Þ", "th"),
        ("ħ", "h"),
        ("Ħ", "h"),
        ("ı", "i"),  # noqa: RUF001 -- Turkish dotless i, not a typo for "i"
    ],
)
def test_expansion_table_entries_fold_to_expected_primary_key(
    char: str, expected_primary: str
) -> None:
    primary, _ = german_sort_key(char)
    assert primary == expected_primary


def test_capital_sharp_s_folds_like_lowercase_sharp_s() -> None:
    primary, _ = german_sort_key("ẞTRASSE")
    assert primary.startswith("ss")


def test_o_with_stroke_sorts_under_o_not_after_s() -> None:
    # Same failure mode as Öztürk: naive codepoint sort puts ø (U+00F8) after every ASCII
    # letter, stranding a Scandinavian surname at the end of an S-heavy neighbourhood.
    names = ["Szabo", "Sørensen", "Sorge"]
    naive = sorted(names)
    assert naive[-1] == "Sørensen"

    corrected = sorted(names, key=german_sort_key)
    # "sorensen" < "sorge": 'e' < 'g' at the first differing position after "sor".
    assert corrected == ["Sørensen", "Sorge", "Szabo"]


# --------------------------------------------------------------------------------------
# Nobiliary particles are NOT reordered (§6: sort as printed)
# --------------------------------------------------------------------------------------


def test_nobiliary_particle_is_not_reordered() -> None:
    names = ["Arendelle, Anna", "von Arendelle", "Amsel"]
    primary, _ = german_sort_key("von Arendelle")
    assert primary.startswith("v")
    assert sorted(names, key=german_sort_key) == [
        "Amsel",
        "Arendelle, Anna",
        "von Arendelle",
    ]


# --------------------------------------------------------------------------------------
# Case-insensitivity
# --------------------------------------------------------------------------------------


def test_case_insensitive_primary_key() -> None:
    lower_primary, _ = german_sort_key("von arendelle")
    upper_primary, _ = german_sort_key("Von Arendelle")
    assert lower_primary == upper_primary


def test_case_insensitive_with_umlaut_all_caps() -> None:
    """Registration PDFs routinely print surnames in all caps (e.g. "OEZTUERK" vs "Öztürk"
    style inconsistency); this exercises mark-stripping and casefold() together, not just
    casefold() alone on a plain-ASCII name."""
    lower_primary, _ = german_sort_key("öztürk")
    upper_primary, _ = german_sort_key("ÖZTÜRK")
    assert lower_primary == upper_primary == "ozturk"


# --------------------------------------------------------------------------------------
# Determinism / stability
# --------------------------------------------------------------------------------------


def test_diacritic_only_difference_does_not_compare_equal() -> None:
    assert german_sort_key("Müller") != german_sort_key("Muller")


def test_sorting_twice_gives_the_same_result() -> None:
    names = ["Öztürk", "Zimmermann", "Müller", "von Arendelle", "Évora", "Straßer"]
    first = sorted(names, key=german_sort_key)
    second = sorted(names, key=german_sort_key)
    assert first == second


# --------------------------------------------------------------------------------------
# Contrast with a naive codepoint sort — the bug this module exists to prevent
# --------------------------------------------------------------------------------------


def test_naive_sort_misplaces_oeztuerk_but_german_sorted_does_not() -> None:
    names = ["Zimmermann", "Öztürk", "Obermeier", "Ostermann"]
    naive = sorted(names)
    assert naive[-1] == "Öztürk"  # codepoint sort strands it after every plain-ASCII name

    corrected = sorted(names, key=german_sort_key)
    assert corrected[-1] == "Zimmermann"
    assert corrected.index("Öztürk") < corrected.index("Zimmermann")


# --------------------------------------------------------------------------------------
# Edge cases
# --------------------------------------------------------------------------------------


def test_empty_string() -> None:
    assert german_sort_key("") == ("", "")


def test_diacritics_only_name() -> None:
    primary, original = german_sort_key("ÖÄÜ")
    assert primary == "oau"
    assert original == "ÖÄÜ"


def test_hyphenated_and_apostrophe_names() -> None:
    names = ["Meyer-Schmidt", "Meyerhoff", "O'Brien", "Oberon"]
    result = sorted(names, key=german_sort_key)
    # "Meyer-Schmidt" precedes "Meyerhoff": '-' (U+002D) < 'h' (U+0068).
    assert result.index("Meyer-Schmidt") < result.index("Meyerhoff")
    # "O'Brien" precedes "Oberon": "'" (U+0027) < 'b' (U+0062).
    assert result.index("O'Brien") < result.index("Oberon")


def test_leading_and_trailing_whitespace_is_preserved_not_stripped() -> None:
    primary, original = german_sort_key("  Öztürk  ")
    assert primary == "  ozturk  "
    assert original == "  Öztürk  "


# --------------------------------------------------------------------------------------
# german_sorted() convenience wrapper
# --------------------------------------------------------------------------------------


@dataclass
class _Student:
    last_name: str


def test_german_sorted_wraps_arbitrary_objects_by_key() -> None:
    students = [
        _Student("Zimmermann"),
        _Student("Öztürk"),
        _Student("Obermeier"),
        _Student("Ostermann"),
    ]
    result = german_sorted(students, key=lambda s: s.last_name)
    assert [s.last_name for s in result] == [
        "Obermeier",
        "Ostermann",
        "Öztürk",
        "Zimmermann",
    ]


def test_german_sorted_does_not_mutate_input_order() -> None:
    students = [_Student("Zimmermann"), _Student("Öztürk"), _Student("Amsel")]
    original_order = [s.last_name for s in students]
    german_sorted(students, key=lambda s: s.last_name)
    assert [s.last_name for s in students] == original_order
