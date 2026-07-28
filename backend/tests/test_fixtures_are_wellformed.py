"""§5.2 fixture guard: do the committed synthetic registration PDFs actually test what we think?

These tests read the **committed** PDFs in ``/test_data`` with pdfplumber (primary parser, per
§5.2) and PyMuPDF/``fitz`` (fallback parser) — they must not import ``scripts.make_fixtures``
(that would pull in the ``typst`` binding and make this suite depend on a Typst toolchain being
installed) and must not regenerate anything. Expected values below are literal, independently
re-derived from ``scripts/make_fixtures.py``'s docstrings/data — the duplication between "what
the generator says it wrote" and "what these tests expect" is the point: if someone edits the
generator without regenerating the fixtures (or regenerates them differently than intended), this
suite is what catches it.

Regenerate the fixtures with ``uv run python scripts/make_fixtures.py`` (see
``scripts/README.md``); this test file is unaffected either way since it never imports that
script.
"""

from __future__ import annotations

from pathlib import Path

import fitz  # PyMuPDF — a hard dependency (pyproject.toml), §5.2's fallback parser
import pdfplumber
import pytest

TEST_DATA_DIR = Path(__file__).resolve().parents[2] / "test_data"

REAL_SAMPLE = (
    TEST_DATA_DIR
    / "ZPrf_Grundlagen_der_Informationstechnik_B_Sc_WiIng_ET_IT_WiSe_23_24_TestData.pdf"
)
MULTIPAGE = TEST_DATA_DIR / "registration_synthetic_multipage.pdf"
SECOND_COURSE = TEST_DATA_DIR / "registration_synthetic_second_course.pdf"
DUPLICATE = TEST_DATA_DIR / "registration_synthetic_duplicate_matrikelnummer.pdf"
BROKEN_GAP = TEST_DATA_DIR / "registration_synthetic_broken_gap.pdf"
BROKEN_MISSING_PAGE = TEST_DATA_DIR / "registration_synthetic_broken_missing_page.pdf"

EXPECTED_TABLE_HEADER = [
    "Nr.",
    "Matr.-Nr.",
    "",
    "Nachname",
    "",
    "Vorname",
    "Vers.",
    "Kommentar",
    "Note",
]


# --- Shared parsing helper -----------------------------------------------------------------
# Deliberately independent of anything in app/pdf_import (which doesn't exist yet at the time
# this file was written) or scripts/make_fixtures.py: a small, self-contained "match columns by
# header text" reader, mirroring the approach SPECIFICATION.md §5.2 mandates for the real parser.
# Reused across the real sample and every synthetic fixture below, which is itself the strongest
# evidence that the synthetic layout is close enough to the real one for one parser to handle
# both.


def _rows_by_header(page: pdfplumber.page.Page) -> list[dict[str, str]]:
    """All data rows on a page, keyed by table-header text; skips decorative pseudo-tables.

    Only rows whose ``Nr.`` cell is a plain digit string are kept — this both matches how a real
    parser would validate rows and drops any stray artifact rows (the real sample's page
    contains a couple of empty ``[['']]`` pseudo-tables from letterhead graphics, ahead of the
    actual data table).
    """
    rows: list[dict[str, str]] = []
    for table in page.extract_tables():
        if not table or "Nr." not in table[0]:
            continue
        header = table[0]
        for raw_row in table[1:]:
            row = {name: cell for name, cell in zip(header, raw_row, strict=True) if name}
            if row.get("Nr.", "").strip().isdigit():
                rows.append(row)
    return rows


def _all_rows(pdf_path: Path) -> list[dict[str, str]]:
    with pdfplumber.open(pdf_path) as pdf:
        rows = []
        for page in pdf.pages:
            rows.extend(_rows_by_header(page))
        return rows


def _footer_lines(pdf_path: Path) -> list[str]:
    with pdfplumber.open(pdf_path) as pdf:
        return [(page.extract_text() or "").splitlines()[-1] for page in pdf.pages]


def _header_block_lines(pdf_path: Path, page_index: int) -> list[str]:
    """The lines above the table-header row ('Nr. Matr.-Nr. ...') on a given page."""
    with pdfplumber.open(pdf_path) as pdf:
        text = pdf.pages[page_index].extract_text() or ""
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if line.startswith("Nr."):
            return lines[:i]
    raise AssertionError(f"no table header row found on page {page_index + 1} of {pdf_path.name}")


# --- Cross-check: the shared helper works on the real sample too ----------------------------


def test_helper_matches_the_real_sample() -> None:
    """The header-text-matching approach the synthetic fixtures are designed for also parses
    the one real anonymized sample correctly — this is what "structurally similar enough" means
    in practice, made concrete rather than just asserted."""
    with pdfplumber.open(REAL_SAMPLE) as pdf:
        assert len(pdf.pages) == 1
        header_row = pdf.pages[0].extract_tables()[-1][0]
        assert header_row == EXPECTED_TABLE_HEADER
        rows = _rows_by_header(pdf.pages[0])
    assert rows == [
        {
            "Nr.": "1",
            "Matr.-Nr.": "123456",
            "Nachname": "Mustermann",
            "Vorname": "Max",
            "Vers.": "5",
            "Kommentar": "(angemeldet)",
            "Note": "",
        },
        {
            "Nr.": "2",
            "Matr.-Nr.": "789012",
            "Nachname": "von Arendelle",
            "Vorname": "Leyla Olivia",
            "Vers.": "4",
            "Kommentar": "(angemeldet)",
            "Note": "",
        },
    ]


# --- Fixture 1: multipage -------------------------------------------------------------------


def test_multipage_has_three_pages_with_continuous_footers() -> None:
    with pdfplumber.open(MULTIPAGE) as pdf:
        assert len(pdf.pages) == 3
    assert _footer_lines(MULTIPAGE) == ["Seite 1 von 3", "Seite 2 von 3", "Seite 3 von 3"]


def test_multipage_repeats_header_block_and_table_header_every_page() -> None:
    expected_header_block = [
        "Datum: 22.01.2024, Stand: 09:50:53 Uhr",
        "WiSe 23/24",
        "Termin: 1. Termin",
        "Grundlagen der Informationstechnik (B.Sc. WiIng ET/IT)",
        "Prüfer: Prof.Dr.-Ing. Armin Dekorsy",
    ]
    with pdfplumber.open(MULTIPAGE) as pdf:
        for i, page in enumerate(pdf.pages):
            assert _header_block_lines(MULTIPAGE, i) == expected_header_block
            table_header = [t[0] for t in page.extract_tables() if "Nr." in t[0]]
            assert table_header == [EXPECTED_TABLE_HEADER]


def test_multipage_nr_is_contiguous_1_to_50_across_pages() -> None:
    rows = _all_rows(MULTIPAGE)
    nrs = [int(r["Nr."]) for r in rows]
    assert nrs == list(range(1, 51))


def test_multipage_matrikelnummern_are_obviously_fake_and_unique() -> None:
    rows = _all_rows(MULTIPAGE)
    matrikel = [r["Matr.-Nr."] for r in rows]
    assert len(matrikel) == len(set(matrikel)), "duplicate Matr.-Nr. within one file"
    assert all(m.startswith("999") and len(m) == 7 for m in matrikel)


def test_multipage_contains_required_special_case_rows() -> None:
    rows = {int(r["Nr."]): r for r in _all_rows(MULTIPAGE)}
    # Nobiliary particle + double given name (§6 sort edge cases).
    assert rows[4]["Nachname"] == "von Arendelle"
    assert rows[4]["Vorname"] == "Leyla Olivia"
    # Umlaut that matters for DIN 5007-1 collation (§6).
    assert rows[5]["Nachname"] == "Öztürk"
    # "ß".
    assert rows[6]["Nachname"] == "Groß"
    # Rows flagged for review per §5.3: Kommentar != "(angemeldet)".
    non_angemeldet = {
        nr: r["Kommentar"] for nr, r in rows.items() if r["Kommentar"] != "(angemeldet)"
    }
    assert non_angemeldet == {
        10: "(zurückgetreten)",
        25: "(krank gemeldet)",
        40: "(exmatrikuliert)",
    }


# --- Fixture 2: second course, same exam sitting --------------------------------------------


def test_second_course_is_one_page_with_distinct_title_and_disjoint_matrikel() -> None:
    with pdfplumber.open(SECOND_COURSE) as pdf:
        assert len(pdf.pages) == 1
    assert _footer_lines(SECOND_COURSE) == ["Seite 1 von 1"]

    header_block = _header_block_lines(SECOND_COURSE, 0)
    # Exact equality, not a substring check: the header block must stay 5 lines (title on one
    # physical text line, not wrapped — see scripts/make_fixtures.py's `_one_line`/
    # `module_title_font_size`), and §5.1 requires module_title to be stored verbatim.
    assert header_block == [
        "Datum: 22.01.2024, Stand: 09:50:53 Uhr",
        "WiSe 23/24",  # same semester as fixture 1
        "Termin: 1. Termin",  # same Termin as fixture 1
        "Grundlagen der Informationstechnik für Wirtschaftsingenieurwesen "
        "(B.Sc. WiIng ET/IT M.Sc.), 6 CP, BPO 2020/2024 Kombinationsprüfung",
        "Prüfer: Prof.Dr.-Ing. Armin Dekorsy",
    ]

    rows = _all_rows(SECOND_COURSE)
    assert [int(r["Nr."]) for r in rows] == list(range(1, 16))
    second_course_matrikel = {r["Matr.-Nr."] for r in rows}
    multipage_matrikel = {r["Matr.-Nr."] for r in _all_rows(MULTIPAGE)}
    assert second_course_matrikel.isdisjoint(multipage_matrikel), (
        "fixture 2 is the clean multi-file merge case and must not collide with fixture 1"
    )


def test_second_course_title_survives_the_pymupdf_fallback_parser() -> None:
    """§5.2 names PyMuPDF (``fitz``) as the fallback extraction engine if pdfplumber fails.

    Unlike pdfplumber (which reads the PDF content stream regardless of the visible/cropped
    region), fitz clips text to the page's mediabox — so a long header line rendered wide enough
    to hang off the page edge reads fine under pdfplumber but comes back silently truncated under
    fitz. Guards against that regression specifically (see scripts/make_fixtures.py's
    `_one_line` docstring for the incident this test is named after).
    """
    doc = fitz.open(SECOND_COURSE)
    try:
        text = doc[0].get_text()
    finally:
        doc.close()
    assert (
        "Grundlagen der Informationstechnik für Wirtschaftsingenieurwesen "
        "(B.Sc. WiIng ET/IT M.Sc.), 6 CP, BPO 2020/2024 Kombinationsprüfung"
    ) in text


# --- Fixture 3: duplicate Matrikelnummer across files ----------------------------------------


def test_duplicate_matrikelnummer_shares_exactly_one_row_with_multipage() -> None:
    with pdfplumber.open(DUPLICATE) as pdf:
        assert len(pdf.pages) == 1
    rows = _all_rows(DUPLICATE)
    assert [int(r["Nr."]) for r in rows] == list(range(1, 11))

    duplicate_matrikel = {r["Matr.-Nr."] for r in rows}
    multipage_matrikel = {r["Matr.-Nr."] for r in _all_rows(MULTIPAGE)}
    shared = duplicate_matrikel & multipage_matrikel
    assert shared == {"9990005"}

    dup_row = next(r for r in rows if r["Matr.-Nr."] == "9990005")
    assert dup_row["Nachname"] == "Öztürk"
    assert dup_row["Vorname"] == "Aylin"


# --- Fixture 4: Nr. gap -----------------------------------------------------------------------


def test_broken_gap_fixture_really_has_a_gap() -> None:
    with pdfplumber.open(BROKEN_GAP) as pdf:
        assert len(pdf.pages) == 1
    rows = _all_rows(BROKEN_GAP)
    nrs = [int(r["Nr."]) for r in rows]
    assert len(nrs) == 20
    assert max(nrs) == 21
    assert nrs != list(range(1, max(nrs) + 1)), "fixture no longer has a gap — test is pointless"
    missing = sorted(set(range(1, max(nrs) + 1)) - set(nrs))
    assert missing == [18]


# --- Fixture 5: missing page --------------------------------------------------------------------


def test_broken_missing_page_fixture_really_is_missing_a_page() -> None:
    with pdfplumber.open(BROKEN_MISSING_PAGE) as pdf:
        assert len(pdf.pages) == 2, "exactly 2 physical pages must be present (1 and 3, not 2)"
    footers = _footer_lines(BROKEN_MISSING_PAGE)
    assert footers == ["Seite 1 von 3", "Seite 3 von 3"], (
        "footer must declare 3 pages total while only page 1 and 3 physically exist"
    )

    rows = _all_rows(BROKEN_MISSING_PAGE)
    nrs = [int(r["Nr."]) for r in rows]
    assert nrs == [*range(1, 11), *range(21, 31)]
    missing = sorted(set(range(1, 31)) - set(nrs))
    assert missing == list(range(11, 21)), "the whole dropped page's Nr. range must be missing"


@pytest.mark.parametrize(
    "path",
    [MULTIPAGE, SECOND_COURSE, DUPLICATE, BROKEN_GAP, BROKEN_MISSING_PAGE],
    ids=lambda p: p.name,
)
def test_synthetic_fixtures_exist_and_are_named_committable(path: Path) -> None:
    assert path.is_file(), f"missing fixture: {path}"
    assert "_synthetic" in path.name, "filename must contain '_synthetic' (see root .gitignore)"
