"""Registration-PDF parser (SPECIFICATION.md §5).

Pure parsing layer: no database, no FastAPI, no I/O beyond the ``bytes`` handed in. The result
is a plain dataclass the import API can map onto ``StudentRegistration`` rows (§4) however it
likes.

Two rules dominate the design:

* **Columns are matched by header text, never by fixed position** (§5.2, §14 #1). The one real
  sample reports nine physical columns — ``['Nr.', 'Matr.-Nr.', '', 'Nachname', '', 'Vorname',
  'Vers.', 'Kommentar', 'Note']``, with two empty interleaved ones — and other administrative
  offices' exports may differ again. An unrecognised layout fails loudly.
* **A partial list is never returned** (§5.3). Either the §5.3 completeness check passes and a
  complete ``ParsedFile`` comes back, or an exception is raised carrying no rows at all.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from app.pdf_import.errors import (
    PdfHeaderError,
    PdfLayoutError,
    RegistrationCompletenessError,
    ScannedPdfError,
)
from app.pdf_import.extraction import RawPage, extract_with_pdfplumber, extract_with_pymupdf

__all__ = ["Engine", "ParsedFile", "ParsedRow", "parse_registration_pdf"]

Engine = Literal["auto", "pdfplumber", "pymupdf"]

# The only Kommentar value that counts as "normally registered" (§5.3). The whitelist is
# deliberately this narrow: over-flagging costs the instructor one glance at the UI, while
# under-flagging puts e.g. a "(zurückgetreten)" student silently onto the attendance list.
NORMAL_KOMMENTAR = "(angemeldet)"

# Header label (normalised: whitespace collapsed, casefolded) -> field name. Small alias set for
# the wording drift §14 #1 anticipates; anything unknown is ignored, and a *missing required*
# column is a hard error rather than a guess based on position.
_COLUMN_ALIASES: dict[str, str] = {
    "nr.": "nr",
    "nr": "nr",
    "lfd. nr.": "nr",
    "lfd.nr.": "nr",
    "matr.-nr.": "matrikelnummer",
    "matr.nr.": "matrikelnummer",
    "matrikelnummer": "matrikelnummer",
    "matrikel-nr.": "matrikelnummer",
    "nachname": "nachname",
    "name": "nachname",
    "familienname": "nachname",
    "vorname": "vorname",
    "vornamen": "vorname",
    "vers.": "versuch",
    "vers": "versuch",
    "versuch": "versuch",
    "kommentar": "kommentar",
    "bemerkung": "kommentar",
    "note": "note",
}
_REQUIRED_COLUMNS = ("nr", "matrikelnummer", "nachname", "vorname", "versuch", "kommentar")
_GERMAN_COLUMN_LABEL = {
    "nr": "Nr.",
    "matrikelnummer": "Matr.-Nr.",
    "nachname": "Nachname",
    "vorname": "Vorname",
    "versuch": "Vers.",
    "kommentar": "Kommentar",
}

_FOOTER_RE = re.compile(r"Seite\s+(\d+)\s+von\s+(\d+)", re.IGNORECASE)
_TERMIN_RE = re.compile(r"^\s*Termin\s*:\s*(.+?)\s*$", re.IGNORECASE)
_PRUEFER_RE = re.compile(r"^\s*Pr[üu]fer(?:in|/in)?\s*:\s*(.+?)\s*$", re.IGNORECASE)
_DATUM_RE = re.compile(r"Datum\s*:\s*([^,]+)")
_STAND_RE = re.compile(r"Stand\s*:\s*(.+?)(?:\s*Uhr)?\s*$")
_SEMESTER_RE = re.compile(r"(?<!\w)(?:WiSe|SoSe|WS|SS)\.?\s*\d{2}(?:\s*/\s*\d{2})?(?!\w)")
_PARENTHETICAL_RE = re.compile(r"\(([^()]*)\)")


@dataclass(frozen=True, slots=True)
class ParsedRow:
    """One student row of the registration table (§4's ``StudentRegistration``, PDF part)."""

    nr: int
    # Always a string: Matrikelnummern are identifiers, not numbers — leading zeros matter and
    # must survive the round trip (§4).
    matrikelnummer: str
    nachname: str
    vorname: str
    versuch: int
    kommentar: str | None
    flagged: bool


@dataclass(frozen=True, slots=True)
class ParsedFile:
    """Everything one registration PDF contributes to an Exam (§5.1).

    ``module_title`` is the entire title line, verbatim; ``course_code`` is the first
    parenthetical inside it. They are two different things on purpose (§4, §5.1): the former is
    never normalised or cross-checked between the PDFs of one exam (a Kombinationsprüfung
    legitimately names a different module per course), the latter is the short grouping/sort key.
    """

    semester: str
    termin: str
    module_title: str
    course_code: str
    pruefer: str
    rows: tuple[ParsedRow, ...]
    declared_page_count: int
    engine: Engine
    export_datum: str | None = None
    export_stand: str | None = None
    filename: str | None = None

    @property
    def student_count(self) -> int:
        return len(self.rows)


def parse_registration_pdf(
    data: bytes, *, filename: str | None = None, engine: Engine = "auto"
) -> ParsedFile:
    """Parse one registration-export PDF into header metadata plus student rows.

    ``engine`` selects the extraction engine and exists for tests and diagnostics; production
    callers should leave it at ``"auto"``, which uses pdfplumber and falls back to PyMuPDF only
    when pdfplumber finds no usable table (§5.2).

    Raises a subclass of :class:`app.pdf_import.errors.PdfImportError` — with a German,
    user-showable ``message`` — on any failure. Never returns partially parsed data.
    """
    pages, used_engine = _extract(data, engine)

    if not any(page.lines for page in pages):
        raise ScannedPdfError(
            "Aus dieser PDF-Datei konnte kein Text gelesen werden. Vermutlich handelt es sich "
            "um einen Scan bzw. eine reine Bilddatei. Texterkennung (OCR) wird nicht "
            "unterstützt — bitte laden Sie die Original-PDF der Anmeldeliste hoch."
        )
    if not _has_data_rows(pages):
        raise PdfLayoutError(
            "In dieser PDF-Datei wurde keine Anmeldetabelle gefunden. Erwartet wird eine "
            "Tabelle mit den Spalten „Nr.“, „Matr.-Nr.“, „Nachname“, „Vorname“, „Vers.“ und "
            "„Kommentar“."
        )

    metadata_page = next(page for page in pages if page.table)
    header = _parse_header_block(_header_block_lines(metadata_page.lines))

    rows: list[ParsedRow] = []
    for page in pages:
        if page.table:
            rows.extend(_parse_table(page.table, page.number))
    if not rows:
        # A table was found but nothing in it looked like a student row. Loud, per §14 #1 — an
        # empty import is indistinguishable from "everyone was silently dropped".
        raise PdfLayoutError(
            "In der gefundenen Tabelle konnten keine Studierendenzeilen gelesen werden. Die "
            "Datei wurde nicht importiert."
        )

    _validate_completeness(rows, pages)

    return ParsedFile(
        semester=header.semester,
        termin=header.termin,
        module_title=header.module_title,
        course_code=header.course_code,
        pruefer=header.pruefer,
        rows=tuple(rows),
        declared_page_count=_declared_page_count(pages),
        engine=used_engine,
        export_datum=header.export_datum,
        export_stand=header.export_stand,
        filename=filename,
    )


# --- engine selection ---------------------------------------------------------------------


def _has_data_rows(pages: list[RawPage]) -> bool:
    return any(len(page.table) > 1 for page in pages)


def _extract(data: bytes, engine: Engine) -> tuple[list[RawPage], Engine]:
    if engine == "pymupdf":
        return extract_with_pymupdf(data), "pymupdf"
    pages = extract_with_pdfplumber(data)
    if engine == "pdfplumber":
        return pages, "pdfplumber"
    if _has_data_rows(pages):
        return pages, "pdfplumber"
    # pdfplumber found no usable table: try the fallback engine (§5.2). Also switch to it when
    # pdfplumber saw no text at all but PyMuPDF does, so that "looks like a scan" stays a
    # statement about the file rather than about one library.
    fallback = extract_with_pymupdf(data)
    if _has_data_rows(fallback) or (
        not any(page.lines for page in pages) and any(page.lines for page in fallback)
    ):
        return fallback, "pymupdf"
    return pages, "pdfplumber"


# --- header block (§5.1) ------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _HeaderMetadata:
    semester: str
    termin: str
    module_title: str
    course_code: str
    pruefer: str
    export_datum: str | None
    export_stand: str | None


def _header_block_lines(lines: tuple[str, ...]) -> list[str]:
    """The lines above the table-header row, i.e. the repeated 5-line header block (§5.1)."""
    for index, line in enumerate(lines):
        known = sum(1 for token in line.split() if _normalise(token) in _COLUMN_ALIASES)
        if known >= 3:  # order-independent: does not assume "Nr." comes first
            return list(lines[:index])
    return [line for line in lines if not _FOOTER_RE.search(line)]


def _parse_header_block(lines: list[str]) -> _HeaderMetadata:
    termin = _find(lines, _TERMIN_RE)
    pruefer = _find(lines, _PRUEFER_RE)
    if termin is None or pruefer is None:
        missing = ", ".join(
            label for label, found in (("Termin:", termin), ("Prüfer:", pruefer)) if found is None
        )
        raise PdfHeaderError(
            f"Der Kopfbereich der PDF-Datei konnte nicht gelesen werden: Die Zeile „{missing}“ "
            "wurde nicht gefunden."
        )
    termin_index, termin_match = termin
    pruefer_index, pruefer_match = pruefer

    # The title line is identified *structurally* — the line between "Termin:" and "Prüfer:" —
    # never by matching known lecture names: §4 is emphatic that the Lecture name is neither
    # derived from nor validated against the PDF.
    title_lines = [line.strip() for line in lines[termin_index + 1 : pruefer_index] if line.strip()]
    if not title_lines:
        raise PdfHeaderError(
            "Die Titelzeile (Modulname) konnte nicht bestimmt werden: zwischen „Termin:“ und "
            "„Prüfer:“ steht keine Zeile."
        )
    # §14 #9, resolved against a real sample: a long title wraps across several physical lines,
    # e.g. "Grundlagen der Informationstechnik für" / "Wirtschaftsingenieurwesen (B.Sc. WiIng
    # ET/IT) BPO 2020/2024". The region between "Termin:" and "Prüfer:" *is* the title by
    # construction, so every line in it is one wrapped fragment and rejoining them with a single
    # space reconstructs the printed title rather than guessing at one.
    #
    # Known gap: a wrap that hyphenates a word ("Wirtschaftsingenieur-" / "wesen") would come
    # back as "Wirtschaftsingenieur- wesen". No observed export hyphenates — this one wraps at
    # word boundaries — and de-hyphenating blind would corrupt a legitimately hyphenated title
    # ("BPO 2020-" / "2024"). Revisit only with a real sample that exhibits it.
    module_title = " ".join(" ".join(line.split()) for line in title_lines)

    parenthetical = _PARENTHETICAL_RE.search(module_title)
    # First parenthetical only: a Kombinationsprüfung title carries further text after the
    # closing paren, e.g. "..., 6 CP, BPO 2020/2024 Kombinationsprüfung" (§5.1).
    course_code = parenthetical.group(1).strip() if parenthetical else ""
    if not course_code:
        raise PdfHeaderError(
            "Aus der Titelzeile konnte kein Studiengang in Klammern ermittelt werden (erwartet "
            "wird z. B. „… (B.Sc. WiIng ET/IT)“)."
        )

    semester = None
    for line in lines:
        match = _SEMESTER_RE.search(line)
        if match:
            semester = " ".join(match.group(0).split())
            break
    if semester is None:
        raise PdfHeaderError(
            "Im Kopfbereich der PDF-Datei wurde kein Semester gefunden (erwartet wird z. B. "
            "„WiSe 23/24“)."
        )

    datum = stand = None
    for line in lines:
        datum_match = _DATUM_RE.search(line)
        if datum_match and datum is None:
            datum = datum_match.group(1).strip()
        stand_match = _STAND_RE.search(line)
        if stand_match and stand is None:
            stand = stand_match.group(1).strip()

    return _HeaderMetadata(
        semester=semester,
        termin=termin_match.group(1).strip(),
        module_title=module_title,
        course_code=course_code,
        pruefer=pruefer_match.group(1).strip(),
        export_datum=datum,
        export_stand=stand,
    )


def _find(lines: list[str], pattern: re.Pattern[str]) -> tuple[int, re.Match[str]] | None:
    for index, line in enumerate(lines):
        match = pattern.match(line)
        if match:
            return index, match
    return None


# --- table (§5.2) -------------------------------------------------------------------------


def _normalise(text: str) -> str:
    """Collapse whitespace (including cell-internal newlines) and casefold for comparison."""
    return " ".join(text.split()).casefold()


def _clean(text: str) -> str:
    """Every extracted field is whitespace-cleaned here, on the way in.

    §6's collation sort key deliberately does not strip, so a stray leading space would pin a
    name to the top of the printed attendance sheet. Cleaning belongs at the import boundary.
    """
    return " ".join(text.split())


def _column_map(header_row: tuple[str, ...], page: int | None = None) -> dict[str, int]:
    """Map field name -> column index by header *text* (§5.2), never by position (§14 #1)."""
    mapping: dict[str, int] = {}
    for index, cell in enumerate(header_row):
        field = _COLUMN_ALIASES.get(_normalise(cell))
        if field is not None and field not in mapping:
            mapping[field] = index
    missing = [field for field in _REQUIRED_COLUMNS if field not in mapping]
    if missing:
        labels = ", ".join(f"„{_GERMAN_COLUMN_LABEL[field]}“" for field in missing)
        found = ", ".join(f"„{_clean(cell)}“" for cell in header_row if _clean(cell)) or "keine"
        raise PdfLayoutError(
            f"Die Tabelle{_on_page(page)} hat ein unbekanntes Format: Es fehlen die Spalten "
            f"{labels}. Gefundene Spaltenüberschriften: {found}. Die Datei wurde nicht "
            "importiert."
        )
    return mapping


def _on_page(page: int | None) -> str:
    """ " (auf Seite N)" — page numbers matter: a 3-page export needs to say *which* page."""
    return "" if page is None else f" auf Seite {page}"


def _cell(row: tuple[str, ...], mapping: dict[str, int], field: str) -> str:
    index = mapping[field]
    return _clean(row[index]) if index < len(row) else ""


def _parse_table(table: tuple[tuple[str, ...], ...], page: int | None = None) -> list[ParsedRow]:
    mapping = _column_map(table[0], page)
    rows: list[ParsedRow] = []
    for raw_row in table[1:]:

        def cell(field: str, row: tuple[str, ...] = raw_row) -> str:
            return _cell(row, mapping, field)

        nr_text = cell("nr")
        if not nr_text.isdigit():
            # Not a data row: the table header repeated on pages 2..N, a blank spacer row, or a
            # stray fragment the fallback engine clustered into the table band. Skipping is safe
            # *because* §5.3's contiguity check below is the net: a real data row lost here
            # resurfaces as a missing Nr. and hard-fails the import with a useful message.
            continue

        matrikelnummer = cell("matrikelnummer")
        nachname = cell("nachname")
        versuch_text = cell("versuch")
        # These, by contrast, cannot be caught by the contiguity check — the row is present and
        # numbered but unusable — so they fail loudly (§14 #1). No cell contents in the message:
        # they may hold personal data (CLAUDE.md).
        if not matrikelnummer or not nachname or not versuch_text.isdigit():
            raise PdfLayoutError(
                f"Die Zeile mit der laufenden Nummer {int(nr_text)}{_on_page(page)} konnte "
                "nicht gelesen werden (Matr.-Nr., Nachname oder Vers. fehlt bzw. ist unerwartet "
                "formatiert). Die Datei wurde nicht importiert."
            )

        kommentar = cell("kommentar")
        rows.append(
            ParsedRow(
                nr=int(nr_text),
                matrikelnummer=matrikelnummer,
                nachname=nachname,
                vorname=cell("vorname"),
                versuch=int(versuch_text),
                kommentar=kommentar or None,
                flagged=kommentar.casefold() != NORMAL_KOMMENTAR,
            )
        )
    return rows


# --- §5.3 mandatory post-parse validation ---------------------------------------------------


def _declared_page_count(pages: list[RawPage]) -> int:
    counts = {total for _, total in _footers(pages)}
    return counts.pop() if len(counts) == 1 else len(pages)


def _footers(pages: list[RawPage]) -> list[tuple[int, int]]:
    """``(printed page number, declared total)`` for every page that carries a footer."""
    footers: list[tuple[int, int]] = []
    for page in pages:
        for line in reversed(page.lines):
            match = _FOOTER_RE.search(line)
            if match:
                footers.append((int(match.group(1)), int(match.group(2))))
                break
    return footers


def _validate_completeness(rows: list[ParsedRow], pages: list[RawPage]) -> None:
    """§5.3's mandatory check. Hard failure, never a warning.

    Two independent things are verified:

    1. The parsed ``Nr.`` values form a contiguous ``1..N`` with no gaps and no duplicates, and
       ``N`` equals the highest parsed ``Nr.``.
    2. Every page the ``Seite X von Y`` footer declares is physically present and was parsed:
       the set of printed page numbers must equal ``{1..Y}``, with one consistent ``Y``.

    §5.3 asks that ``N`` additionally equal "the page footer's declared count". The real
    export's footer declares *pages*, not rows, and the format carries no explicit row total
    anywhere — so check 2 is how that requirement is realised here: a dropped page (the failure
    mode §5.3 actually names) is caught twice over, once as a hole in the Nr. sequence and once
    as a missing page number, and neither check can be satisfied by a partially parsed file.
    """
    problems: list[str] = []

    nrs = [row.nr for row in rows]
    highest = max(nrs)
    seen = set(nrs)
    missing_nrs = tuple(nr for nr in range(1, highest + 1) if nr not in seen)
    duplicate_nrs = tuple(sorted({nr for nr in nrs if nrs.count(nr) > 1}))
    if missing_nrs:
        subject = (
            f"Es fehlt die laufende Nummer {_format_numbers(missing_nrs)}"
            if len(missing_nrs) == 1
            else f"Es fehlen die laufenden Nummern {_format_numbers(missing_nrs)}"
        )
        problems.append(f"{subject} (erwartet: 1 bis {highest}, gefunden: {len(seen)} Zeilen).")
    if duplicate_nrs:
        problems.append(
            f"Die laufende Nummer {_format_numbers(duplicate_nrs)} kommt mehrfach vor."
            if len(duplicate_nrs) == 1
            else f"Die laufenden Nummern {_format_numbers(duplicate_nrs)} kommen mehrfach vor."
        )

    footers = _footers(pages)
    missing_pages: tuple[int, ...] = ()
    duplicate_pages: tuple[int, ...] = ()
    declared: int | None = None
    if len(footers) != len(pages):
        problems.append(
            f"Auf {len(pages) - len(footers)} Seite(n) fehlt die Fußzeile „Seite X von Y“, "
            "die Vollständigkeit lässt sich daher nicht prüfen."
        )
    else:
        totals = {total for _, total in footers}
        if len(totals) > 1:
            problems.append(
                "Die Fußzeilen widersprechen sich: Es werden unterschiedliche Gesamtseitenzahlen "
                f"angegeben ({_format_numbers(tuple(sorted(totals)))})."
            )
        else:
            declared = totals.pop()
            printed = [number for number, _ in footers]
            missing_pages = tuple(
                number for number in range(1, declared + 1) if number not in set(printed)
            )
            duplicate_pages = tuple(sorted({p for p in printed if printed.count(p) > 1}))
            if missing_pages:
                lack = (
                    f"es fehlt aber Seite {_format_numbers(missing_pages)}"
                    if len(missing_pages) == 1
                    else f"es fehlen aber die Seiten {_format_numbers(missing_pages)}"
                )
                problems.append(f"Laut Fußzeile besteht die Datei aus {declared} Seiten, {lack}.")
            if duplicate_pages:
                problems.append(
                    f"Seite {_format_numbers(duplicate_pages)} kommt mehrfach vor."
                    if len(duplicate_pages) == 1
                    else f"Die Seiten {_format_numbers(duplicate_pages)} kommen mehrfach vor."
                )

    if problems:
        raise RegistrationCompletenessError(
            "Die Anmeldeliste ist unvollständig und wurde nicht importiert. "
            + " ".join(problems)
            + " Bitte prüfen Sie die PDF-Datei und laden Sie sie vollständig erneut hoch.",
            missing_nrs=missing_nrs,
            duplicate_nrs=duplicate_nrs,
            missing_pages=missing_pages,
            duplicate_pages=duplicate_pages,
            declared_page_count=declared,
        )


def _format_numbers(numbers: tuple[int, ...]) -> str:
    """``(11, ..., 20, 25)`` -> ``"11 bis 20, 25"`` — readable for an instructor."""
    parts: list[str] = []
    start = previous = numbers[0]
    for number in (*numbers[1:], None):
        if number is not None and number == previous + 1:
            previous = number
            continue
        parts.append(str(start) if start == previous else f"{start} bis {previous}")
        if number is not None:
            start = previous = number
    return ", ".join(parts)
