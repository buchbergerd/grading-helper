"""Registration-PDF parser tests (SPECIFICATION.md §5).

Covers the one real anonymized sample, the committed synthetic fixtures (see
``backend/scripts/README.md``), and PDFs constructed on the fly for the failure modes no fixture
provides (a scan, an unknown table layout, a damaged file).

The parser is pure and database-free, so nothing here needs the ``conftest.py`` DB fixtures.
"""

from __future__ import annotations

from pathlib import Path

import fitz  # PyMuPDF — used here only to *build* the synthetic failure-case PDFs
import pytest

from app.pdf_import import (
    NORMAL_KOMMENTAR,
    Engine,
    ParsedFile,
    ParsedRow,
    PdfHeaderError,
    PdfImportError,
    PdfLayoutError,
    RegistrationCompletenessError,
    ScannedPdfError,
    UnreadablePdfError,
    parse_registration_pdf,
)
from app.pdf_import.parser import _parse_header_block, _parse_table

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

REAL_TITLE = "Grundlagen der Informationstechnik (B.Sc. WiIng ET/IT)"
SECOND_COURSE_TITLE = (
    "Grundlagen der Informationstechnik für Wirtschaftsingenieurwesen "
    "(B.Sc. WiIng ET/IT M.Sc.), 6 CP, BPO 2020/2024 Kombinationsprüfung"
)


def parse(path: Path, engine: Engine = "auto") -> ParsedFile:
    return parse_registration_pdf(path.read_bytes(), filename=path.name, engine=engine)


# --- the real sample (§5.1) ------------------------------------------------------------------


def test_real_sample_header_metadata() -> None:
    result = parse(REAL_SAMPLE)

    assert result.semester == "WiSe 23/24"
    assert result.termin == "1. Termin"
    assert result.pruefer == "Prof.Dr.-Ing. Armin Dekorsy"
    assert result.module_title == REAL_TITLE  # verbatim, never normalised (§4, §5.1)
    assert result.course_code == "B.Sc. WiIng ET/IT"
    assert result.export_datum == "22.01.2024"
    assert result.export_stand == "09:50:53"
    assert result.declared_page_count == 1
    assert result.filename == REAL_SAMPLE.name
    assert result.engine == "pdfplumber"  # primary engine handles the real export (§5.2)


def test_real_sample_rows() -> None:
    result = parse(REAL_SAMPLE)

    assert result.student_count == 2
    assert result.rows == (
        ParsedRow(
            nr=1,
            matrikelnummer="123456",
            nachname="Mustermann",
            vorname="Max",
            versuch=5,
            kommentar="(angemeldet)",
            flagged=False,
        ),
        ParsedRow(
            nr=2,
            matrikelnummer="789012",
            # Kept exactly as printed — §6 sorts names as printed, no nobiliary-particle
            # reordering, so the parser must not "tidy" this either.
            nachname="von Arendelle",
            vorname="Leyla Olivia",
            versuch=4,
            kommentar="(angemeldet)",
            flagged=False,
        ),
    )


def test_matrikelnummer_stays_a_string() -> None:
    """Matrikelnummern are identifiers, not numbers — leading zeros must survive (§4)."""
    for row in parse(MULTIPAGE).rows:
        assert isinstance(row.matrikelnummer, str)
    assert all(isinstance(row.matrikelnummer, str) for row in parse(REAL_SAMPLE).rows)
    assert parse(REAL_SAMPLE).rows[0].matrikelnummer == "123456"


def test_versuch_is_an_int() -> None:
    assert [row.versuch for row in parse(REAL_SAMPLE).rows] == [5, 4]


# --- multi-page: the normal case (§5.2) -------------------------------------------------------


def test_multipage_yields_all_fifty_rows_across_three_pages() -> None:
    result = parse(MULTIPAGE)

    assert result.declared_page_count == 3
    assert result.student_count == 50
    assert [row.nr for row in result.rows] == list(range(1, 51))


def test_multipage_contains_rows_from_the_second_and_third_page() -> None:
    rows = {row.nr: row for row in parse(MULTIPAGE).rows}

    # Page 2 starts at Nr. 19, page 3 ends at Nr. 50 (see scripts/make_fixtures.py).
    assert rows[19].nachname == "Nachtigall"
    assert rows[19].matrikelnummer == "9990019"
    assert rows[50].matrikelnummer == "9990050"
    assert rows[4].nachname == "von Arendelle"
    assert rows[5].nachname == "Öztürk"
    assert rows[6].nachname == "Groß"


def test_multipage_does_not_leak_repeated_header_rows_into_the_data() -> None:
    """The 5-line header block and the table-header row repeat on pages 2..N (§5.2)."""
    rows = parse(MULTIPAGE).rows

    assert len(rows) == 50, "a leaked header row would make this 52"
    header_texts = {"Nr.", "Matr.-Nr.", "Nachname", "Vorname", "Vers.", "Kommentar", "Note"}
    for row in rows:
        assert row.nachname not in header_texts
        assert row.vorname not in header_texts
        assert row.matrikelnummer not in header_texts
        assert row.matrikelnummer.isdigit()
        assert "Prüfer" not in row.nachname and "Termin" not in row.nachname


def test_multipage_flags_exactly_the_non_angemeldet_rows() -> None:
    """§5.3: a Kommentar that isn't the normal registered status is flagged, never dropped."""
    rows = {row.nr: row for row in parse(MULTIPAGE).rows}

    flagged = {nr: row.kommentar for nr, row in rows.items() if row.flagged}
    assert flagged == {
        10: "(zurückgetreten)",
        25: "(krank gemeldet)",
        40: "(exmatrikuliert)",
    }
    assert all(row.kommentar == NORMAL_KOMMENTAR for row in rows.values() if not row.flagged)
    assert len(rows) == 50


# --- second course: Kombinationsprüfung title (§4, §5.1) --------------------------------------


def test_second_course_module_title_is_captured_verbatim() -> None:
    result = parse(SECOND_COURSE)

    assert result.module_title == SECOND_COURSE_TITLE
    assert result.module_title.endswith(", 6 CP, BPO 2020/2024 Kombinationsprüfung")


def test_second_course_code_is_only_the_first_parenthetical() -> None:
    result = parse(SECOND_COURSE)

    assert result.course_code == "B.Sc. WiIng ET/IT M.Sc."
    assert "6 CP" not in result.course_code
    assert "Kombinationsprüfung" not in result.course_code


def test_second_course_shares_semester_and_termin_with_the_multipage_file() -> None:
    """§5.3 cross-checks semester/Termin between a Exam's files — the parser supplies them."""
    second, multipage = parse(SECOND_COURSE), parse(MULTIPAGE)

    assert (second.semester, second.termin) == (multipage.semester, multipage.termin)
    assert second.module_title != multipage.module_title  # deliberately *not* normalised


def test_duplicate_matrikelnummer_fixture_parses_cleanly_within_itself() -> None:
    """Cross-file duplicate detection is the import API's job; one file alone is valid (§5.3)."""
    result = parse(DUPLICATE)

    assert result.student_count == 10
    assert "9990005" in {row.matrikelnummer for row in result.rows}


# --- §5.3 mandatory validation: hard failures -------------------------------------------------


def test_gap_in_the_nr_sequence_hard_fails_and_names_the_missing_number() -> None:
    with pytest.raises(RegistrationCompletenessError) as excinfo:
        parse(BROKEN_GAP)

    error = excinfo.value
    assert error.missing_nrs == (18,)
    assert "18" in error.message
    assert "laufende Nummer" in error.message


def test_missing_page_hard_fails_and_names_page_and_missing_nrs() -> None:
    with pytest.raises(RegistrationCompletenessError) as excinfo:
        parse(BROKEN_MISSING_PAGE)

    error = excinfo.value
    assert error.missing_pages == (2,)
    assert error.declared_page_count == 3
    assert error.missing_nrs == tuple(range(11, 21))
    assert "Seite 2" in error.message
    assert "11 bis 20" in error.message


@pytest.mark.parametrize("path", [BROKEN_GAP, BROKEN_MISSING_PAGE], ids=lambda p: p.name)
def test_no_partial_data_escapes_a_validation_failure(path: Path) -> None:
    """§5.3: the caller must be unable to accidentally import a partially parsed list."""
    with pytest.raises(RegistrationCompletenessError) as excinfo:
        parse(path)

    error = excinfo.value
    payload = " ".join(str(value) for value in vars(error).values())
    assert not any(isinstance(value, ParsedFile | ParsedRow) for value in vars(error).values())
    assert not any(
        isinstance(value, list | tuple) and any(isinstance(item, ParsedRow) for item in value)
        for value in vars(error).values()
    )
    # No student data anywhere on the exception — neither rows nor names/Matrikelnummern
    # (CLAUDE.md: errors must stay safe to log).
    assert "Mustermann" not in payload
    assert "999" not in payload


@pytest.mark.parametrize("engine", ["auto", "pymupdf"], ids=["auto", "fallback"])
def test_validation_fails_the_same_way_under_both_engines(engine: Engine) -> None:
    for path in (BROKEN_GAP, BROKEN_MISSING_PAGE):
        with pytest.raises(RegistrationCompletenessError):
            parse(path, engine=engine)


# --- engine strategy (§5.2) --------------------------------------------------------------------


def test_pymupdf_fallback_reproduces_the_pdfplumber_result_on_the_real_sample() -> None:
    """§5.2's fallback engine, verified against the *real* export, not just the fixtures.

    ``fitz.Page.get_text()`` returns the real sample's table roughly column-major (its cells are
    filled rects, not a ruled table), so the fallback reconstructs rows from word coordinates
    instead. This test is what makes that claim checkable: same rows, same columns, same
    metadata as the primary engine — including "von Arendelle"/"Leyla Olivia" staying in their
    own columns.
    """
    primary = parse(REAL_SAMPLE)
    fallback = parse(REAL_SAMPLE, engine="pymupdf")

    assert fallback.engine == "pymupdf"
    assert fallback.rows == primary.rows
    assert fallback.rows[1].nachname == "von Arendelle"
    assert fallback.rows[1].vorname == "Leyla Olivia"
    assert (fallback.module_title, fallback.course_code) == (
        primary.module_title,
        primary.course_code,
    )
    assert (fallback.semester, fallback.termin, fallback.pruefer) == (
        primary.semester,
        primary.termin,
        primary.pruefer,
    )


@pytest.mark.parametrize("path", [MULTIPAGE, SECOND_COURSE, DUPLICATE], ids=lambda p: p.name)
def test_pymupdf_fallback_reproduces_the_pdfplumber_result_on_every_good_fixture(
    path: Path,
) -> None:
    primary = parse(path)
    fallback = parse(path, engine="pymupdf")

    assert fallback.rows == primary.rows
    assert fallback.module_title == primary.module_title
    assert fallback.declared_page_count == primary.declared_page_count


# --- constructed failure cases ------------------------------------------------------------------


#: x positions of the columns in PDFs built by :func:`_text_pdf`, in points.
_COLUMN_X = (72.0, 110.0, 190.0, 300.0, 380.0, 420.0, 520.0)


def _text_pdf(*lines: str | tuple[str, ...]) -> bytes:
    """A minimal text-layer PDF: ``str`` becomes a full-width line, ``tuple`` a row of columns.

    Nothing is *drawn* (no ruled table, no cell rects), so pdfplumber finds no table here and the
    ``auto`` engine falls through to the PyMuPDF path (§5.2) — which is what makes these
    constructed cases exercise the fallback engine end to end.
    """
    document = fitz.open()
    page = document.new_page()
    for index, line in enumerate(lines):
        y = 72 + 16 * index
        cells = (line,) if isinstance(line, str) else line
        xs = (72.0,) if isinstance(line, str) else _COLUMN_X
        for x, cell in zip(xs, cells, strict=False):
            page.insert_text((x, y), cell, fontsize=9, fontname="helv")
    try:
        return bytes(document.tobytes())
    finally:
        document.close()


def _image_only_pdf() -> bytes:
    """A page containing a single bitmap and no text layer at all — a stand-in for a scan."""
    document = fitz.open()
    page = document.new_page()
    pixmap = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 400, 200), False)
    pixmap.clear_with(200)
    page.insert_image(fitz.Rect(50, 50, 450, 250), pixmap=pixmap)
    try:
        return bytes(document.tobytes())
    finally:
        document.close()


def test_scanned_pdf_fails_with_a_clear_ocr_message() -> None:
    """§5.2/§14 #2: no OCR in v1 — a scan must fail loudly, not import as an empty list."""
    with pytest.raises(ScannedPdfError) as excinfo:
        parse_registration_pdf(_image_only_pdf(), filename="scan.pdf")

    message = excinfo.value.message
    assert "Scan" in message
    assert "OCR" in message
    assert isinstance(excinfo.value, PdfImportError)


def test_unknown_table_layout_fails_loudly() -> None:
    """§14 #1: an export whose columns we don't recognise must never be silently misimported."""
    data = _text_pdf(
        "Datum: 22.01.2024, Stand: 09:50:53 Uhr",
        "WiSe 23/24",
        "Termin: 1. Termin",
        "Irgendein Modul (B.Sc. Test)",
        "Prüfer: Prof. Dr. Test",
        ("Nr.", "Studentennummer", "Gesamtname", "Anmerkung"),
        ("1", "9990001", "Mustermann", "angemeldet"),
        "Seite 1 von 1",
    )

    with pytest.raises(PdfLayoutError) as excinfo:
        parse_registration_pdf(data, filename="fremdes_format.pdf")

    message = excinfo.value.message
    assert "Nachname" in message and "Vorname" in message and "Vers." in message
    assert "unbekanntes Format" in message


def test_text_pdf_without_any_table_fails_loudly() -> None:
    with pytest.raises(PdfLayoutError) as excinfo:
        parse_registration_pdf(_text_pdf("Ein Brief.", "Kein Tabelleninhalt."), filename="x.pdf")

    assert "keine Anmeldetabelle" in excinfo.value.message


def test_damaged_file_is_reported_as_unreadable() -> None:
    with pytest.raises(UnreadablePdfError):
        parse_registration_pdf(b"das ist gar keine PDF-Datei", filename="kaputt.pdf")


def test_missing_footer_prevents_a_silent_pass() -> None:
    """Without a ``Seite X von Y`` footer the §5.3 page check cannot run — so it hard-fails."""
    data = _text_pdf(
        "Datum: 22.01.2024, Stand: 09:50:53 Uhr",
        "WiSe 23/24",
        "Termin: 1. Termin",
        "Irgendein Modul (B.Sc. Test)",
        "Prüfer: Prof. Dr. Test",
        ("Nr.", "Matr.-Nr.", "Nachname", "Vorname", "Vers.", "Kommentar", "Note"),
        ("1", "9990001", "Mustermann", "Bob", "1", "(angemeldet)", ""),
    )

    with pytest.raises(RegistrationCompletenessError) as excinfo:
        parse_registration_pdf(data, filename="ohne_fusszeile.pdf")

    assert "Fußzeile" in excinfo.value.message


def test_a_plain_text_pdf_with_a_valid_layout_parses_through_the_fallback_engine() -> None:
    """Guards the negative tests above: the constructed-PDF helper itself is not the reason
    they fail — a well-formed one parses fine (and via the fallback, since it has no table)."""
    data = _text_pdf(
        "Datum: 22.01.2024, Stand: 09:50:53 Uhr",
        "WiSe 23/24",
        "Termin: 1. Termin",
        "Irgendein Modul (B.Sc. Test)",
        "Prüfer: Prof. Dr. Test",
        ("Nr.", "Matr.-Nr.", "Nachname", "Vorname", "Vers.", "Kommentar", "Note"),
        ("1", "9990001", "Mustermann", "Bob", "1", "(angemeldet)", ""),
        ("2", "9990002", "Musterfrau", "Nora", "2", "(krank)", ""),
        "Seite 1 von 1",
    )

    result = parse_registration_pdf(data, filename="ok.pdf")

    assert result.engine == "pymupdf"
    assert [(row.nr, row.matrikelnummer, row.flagged) for row in result.rows] == [
        (1, "9990001", False),
        (2, "9990002", True),
    ]


# --- unit tests on the pure table/header helpers -------------------------------------------------


def test_columns_are_matched_by_header_text_not_position() -> None:
    """§5.2/§14 #1: a reordered export with extra columns must still parse correctly."""
    table = (
        ("Note", "Kommentar", "Vers.", "Vorname", "", "Nachname", "Matr.-Nr.", "Nr."),
        ("", "(angemeldet)", "3", "Bob", "", "Mustermann", "0099", "1"),
    )

    rows = _parse_table(table)

    assert rows == [
        ParsedRow(
            nr=1,
            matrikelnummer="0099",  # leading zeros preserved
            nachname="Mustermann",
            vorname="Bob",
            versuch=3,
            kommentar="(angemeldet)",
            flagged=False,
        )
    ]


def test_every_field_is_whitespace_stripped_on_the_way_in() -> None:
    """§6's sort key deliberately does not strip — cleaning has to happen here (§5.2)."""
    table = (
        (" Nr. ", "Matr.-Nr.", "", "Nachname", "", "Vorname", "Vers.", "Kommentar", "Note"),
        (
            " 1 ",
            " 9990001 ",
            "",
            "  von Arendelle ",
            "",
            "Leyla\nOlivia",
            " 2 ",
            " (angemeldet) ",
            "",
        ),
    )

    (row,) = _parse_table(table)

    assert row.matrikelnummer == "9990001"
    assert row.nachname == "von Arendelle"
    assert row.vorname == "Leyla Olivia"
    assert row.kommentar == "(angemeldet)"
    assert row.flagged is False
    assert row.versuch == 2


def test_repeated_header_and_blank_rows_are_dropped_but_unusable_rows_are_not() -> None:
    header = ("Nr.", "Matr.-Nr.", "Nachname", "Vorname", "Vers.", "Kommentar", "Note")
    ok = ("1", "9990001", "Mustermann", "Bob", "1", "(angemeldet)", "")

    assert len(_parse_table((header, ok, header, ("", "", "", "", "", "", "")))) == 1

    with pytest.raises(PdfLayoutError) as excinfo:
        _parse_table((header, ("2", "", "Musterfrau", "Nora", "1", "(angemeldet)", "")))
    assert "2" in excinfo.value.message
    assert "Musterfrau" not in excinfo.value.message  # no student data in error messages


@pytest.mark.parametrize(
    ("kommentar", "flagged"),
    [
        ("(angemeldet)", False),
        ("(ANGEMELDET)", False),
        ("(zurückgetreten)", True),
        ("(krank gemeldet)", True),
        ("angemeldet", True),  # deliberately narrow whitelist: over-flag rather than under-flag
        ("", True),
    ],
)
def test_flagging_rule(kommentar: str, flagged: bool) -> None:
    header = ("Nr.", "Matr.-Nr.", "Nachname", "Vorname", "Vers.", "Kommentar", "Note")
    (row,) = _parse_table((header, ("1", "9990001", "Mustermann", "Bob", "1", kommentar, "")))

    assert row.flagged is flagged
    assert row.kommentar == (kommentar or None)


def test_layout_errors_name_the_page_they_happened_on() -> None:
    """A 50-student export spans several pages — "which page?" is the first thing the
    instructor needs in order to look at the file (§5.2 multi-page is the normal case)."""
    header = ("Nr.", "Matr.-Nr.", "Nachname", "Vorname", "Vers.", "Kommentar", "Note")

    with pytest.raises(PdfLayoutError) as excinfo:
        _parse_table((("Nr.", "Etwas", "Anderes"), ("1", "x", "y")), page=3)
    assert "auf Seite 3" in excinfo.value.message

    with pytest.raises(PdfLayoutError) as excinfo:
        _parse_table(
            (header, ("7", "9990007", "Musterfrau", "Nora", "x", "(angemeldet)", "")), page=2
        )
    assert "auf Seite 2" in excinfo.value.message
    assert "7" in excinfo.value.message


def test_header_block_requires_exactly_one_title_line() -> None:
    """§14 #9 leaves a wrapped title line open — guessing at a reassembly is worse than failing."""
    with pytest.raises(PdfHeaderError) as excinfo:
        _parse_header_block(
            [
                "WiSe 23/24",
                "Termin: 1. Termin",
                "Grundlagen der Informationstechnik für ganz viele",
                "Wirtschaftsingenieure (B.Sc. WiIng ET/IT)",
                "Prüfer: Prof. Dr. Test",
            ]
        )

    assert "Titelzeile" in excinfo.value.message


def test_header_block_requires_a_parenthetical_course_code() -> None:
    with pytest.raises(PdfHeaderError) as excinfo:
        _parse_header_block(
            [
                "WiSe 23/24",
                "Termin: 1. Termin",
                "Grundlagen der Informationstechnik ohne Studiengang",
                "Prüfer: Prof. Dr. Test",
            ]
        )

    assert "Studiengang" in excinfo.value.message


def test_header_block_requires_semester_termin_and_pruefer() -> None:
    with pytest.raises(PdfHeaderError):
        _parse_header_block(["WiSe 23/24", "Ein Modul (B.Sc. Test)", "Prüfer: Prof. Dr. Test"])
    with pytest.raises(PdfHeaderError) as excinfo:
        _parse_header_block(
            ["Termin: 1. Termin", "Ein Modul (B.Sc. Test)", "Prüfer: Prof. Dr. Test"]
        )
    assert "Semester" in excinfo.value.message


def test_header_block_parses_a_summer_semester_and_second_termin() -> None:
    header = _parse_header_block(
        [
            "Datum: 01.09.2025, Stand: 12:00:00 Uhr",
            "SoSe 25",
            "Termin: 2. Termin",
            "Ein anderes Modul (M.Sc. ET)",
            "Prüferin: Prof. Dr. Beispiel",
        ]
    )

    assert header.semester == "SoSe 25"
    assert header.termin == "2. Termin"
    assert header.course_code == "M.Sc. ET"
    assert header.pruefer == "Prof. Dr. Beispiel"
    assert header.export_datum == "01.09.2025"


def test_dropping_the_last_page_is_caught_by_the_footer_check_alone() -> None:
    """The §5.3 failure that the Nr.-contiguity check on its own cannot see.

    Losing a page from the *middle* leaves a hole in the ``Nr.`` sequence, so either check
    catches it. Losing the *last* page does not: the surviving rows are a clean, contiguous
    ``1..N`` with nothing visibly wrong — exactly the "a student never gets a grade, with
    nothing visibly wrong" scenario §5.3 names as the worst realistic failure mode. Only the
    ``Seite X von Y`` footer check stands between that file and a silent partial import, which
    is why §5.3's page check is mandatory rather than redundant.
    """
    document = fitz.open(MULTIPAGE)
    document.delete_page(document.page_count - 1)
    truncated = document.tobytes()

    with pytest.raises(RegistrationCompletenessError) as excinfo:
        parse_registration_pdf(truncated)

    error = excinfo.value
    assert error.missing_nrs == (), "the surviving Nr. sequence is contiguous — that is the point"
    assert error.missing_pages == (3,)
    assert "Seite 3" in error.message
