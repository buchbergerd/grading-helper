"""The printed attendance list (SPECIFICATION.md §6).

The module splits deliberately in two halves so the interesting half is testable without a
database, a request or a PDF parser:

``build_attendance_list_data(exam)``
    ORM ``Exam`` → a plain JSON-serialisable :class:`AttendanceListData`. This is where §6's
    rules live — excluded students dropped (§5.3), German (DIN 5007-1) sort, German date
    formatting (§14 #6) — and it is what the sort tests assert against, so an ordering
    regression fails on an exact list comparison rather than on fuzzy PDF text.
``render_attendance_list(data)``
    :class:`AttendanceListData` → PDF bytes. A pure function of its argument; it never touches
    the database. The data crosses into ``templates/attendance_list.typ`` as one JSON string via
    Typst's ``sys.inputs``, which is the "clean templating from JSON-like data" property
    SPECIFICATION.md §12 chose Typst for.

Rendering is offline by construction (§13): the template imports no ``@preview`` package, and
``ignore_system_fonts=True`` pins output to the fonts embedded in the typst binary itself, so a
development machine that happens to have extra fonts installed cannot produce a layout the
Docker image can't reproduce.
"""

from __future__ import annotations

import json
import unicodedata
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import TypedDict
from urllib.parse import quote

import typst

from app.collation import german_sort_key
from app.models import Exam, StudentRegistration

TEMPLATE_PATH = Path(__file__).parent / "templates" / "attendance_list.typ"


class AttendanceListStudent(TypedDict):
    """One printed row — §6's four columns, in §6's order."""

    course_code: str
    matrikelnummer: str
    nachname: str
    vorname: str


class AttendanceListCourse(TypedDict):
    """Per-Studiengang head count, shown in the header block when there is more than one."""

    course_code: str
    count: int


class AttendanceListData(TypedDict):
    """Everything the Typst template needs. JSON-serialisable by construction.

    ``exam_date`` is already a German ``DD.MM.YYYY`` string (or ``None``): the template must not
    have to know about date formatting, and §14 #6 fixes the format for every report.
    """

    lecture_name: str
    semester: str
    termin: str
    exam_date: str | None
    head_count: int
    courses: list[AttendanceListCourse]
    students: list[AttendanceListStudent]


def format_german_date(value: date | None) -> str | None:
    """``DD.MM.YYYY``, the format every user-facing output in this app uses (§14 #6)."""
    return None if value is None else f"{value.day:02d}.{value.month:02d}.{value.year:04d}"


def _sort_key(registration: StudentRegistration) -> tuple[tuple[str, str], ...]:
    """§6's sort key: course, then last name, then first name, then Matrikelnummer.

    Every name component goes through :func:`~app.collation.german_sort_key` (DIN 5007-1): a
    plain ``sorted()`` would strand "Öztürk" after "Zimmermann" on the sheet an instructor
    physically ticks names off on. **This is why the sort happens here and not in SQL** — an
    ``ORDER BY nachname`` is exactly that codepoint sort, and it would look correct in a unit
    test that only used ASCII names.

    Note that ``german_sort_key`` returns ``(folded, original)``, so two names that fold alike
    ("Straßer"/"Strasser") are already separated by their original spelling *before* the Vorname
    element is consulted. The Vorname and Matrikelnummer elements therefore only break genuinely
    identical surnames — which is precisely their job: making the printed order deterministic for
    the two "Müller, Anna" and "Müller, Jonas" rows an instructor scans past each other.
    ``matrikelnummer`` is wrapped in the same key type only to keep the tuple homogeneous; it is
    an opaque digit string, so its collation is irrelevant.
    """
    return (
        german_sort_key(registration.course_code),
        german_sort_key(registration.nachname),
        german_sort_key(registration.vorname),
        german_sort_key(registration.matrikelnummer),
    )


def attendance_list_registrations(exam: Exam) -> list[StudentRegistration]:
    """The exam's printable registrations, in §6 order.

    Excluded students are dropped entirely (§5.3: excluded ≠ deleted — the row stays in the
    database and stays auditable, but appears in no list, report or head count).
    """
    printable = [r for r in exam.registrations if not r.excluded]
    return sorted(printable, key=_sort_key)


def build_attendance_list_data(exam: Exam) -> AttendanceListData:
    """The template payload for one exam.

    Reads the already-loaded ``exam.registrations`` collection rather than issuing its own query,
    which keeps it a pure function of the ORM object and trivially callable from a test that
    never commits. An attendance list is at most a few hundred rows, so filtering and sorting in
    Python costs nothing.
    """
    registrations = attendance_list_registrations(exam)

    counts: dict[str, int] = {}
    for registration in registrations:
        counts[registration.course_code] = counts.get(registration.course_code, 0) + 1

    return AttendanceListData(
        lecture_name=exam.lecture.name,
        semester=exam.semester,
        termin=exam.termin,
        exam_date=format_german_date(exam.exam_date),
        head_count=len(registrations),
        # Same order the rows appear in, so the header's per-course counts read top-to-bottom
        # alongside the table.
        courses=[
            AttendanceListCourse(course_code=course_code, count=counts[course_code])
            for course_code in sorted(counts, key=german_sort_key)
        ],
        students=[
            AttendanceListStudent(
                course_code=registration.course_code,
                matrikelnummer=registration.matrikelnummer,
                nachname=registration.nachname,
                vorname=registration.vorname,
            )
            for registration in registrations
        ],
    )


@lru_cache(maxsize=1)
def _template_source() -> bytes:
    """The template, read once. It is a static file that ships with the package."""
    return TEMPLATE_PATH.read_bytes()


def render_attendance_list(data: AttendanceListData) -> bytes:
    """Render the attendance list to PDF bytes. Pure — no database, no request, no filesystem.

    An exam with **zero** printable registrations renders a valid PDF with a head count of 0 and
    an explanatory line, rather than raising: an instructor may legitimately print the sheet
    before importing the registration PDFs, and §6 gives no reason to make that an error.
    """
    return typst.compile(
        _template_source(),
        sys_inputs={"data": json.dumps(data, ensure_ascii=False)},
        # §13: no outbound network calls at runtime. The template imports no @preview package, so
        # there is nothing to fetch; forbidding system fonts additionally guarantees the byte
        # output does not depend on which fonts the host happens to have installed.
        ignore_system_fonts=True,
    )


# --------------------------------------------------------------------------------------------
# Download filename
# --------------------------------------------------------------------------------------------

#: Umlauts/ß expanded the way a German reader expects them in a *filename* (ä → ae, ß → ss).
#: This is display transliteration, not collation — do not reuse ``app.collation`` here: DIN
#: 5007-1 folds "ä" to "a" and case-folds, which would turn "Prüfung" into "prufung".
_ASCII_TRANSLITERATION = {
    "ä": "ae",
    "ö": "oe",
    "ü": "ue",
    "Ä": "Ae",
    "Ö": "Oe",
    "Ü": "Ue",
    "ß": "ss",
}


def sanitize_filename_part(value: str) -> str:
    """One filename component: no path separators, no whitespace, no empty result.

    ``semester`` is free text and realistically contains a slash ("WiSe 23/24"), which would
    otherwise turn the download name into a path.

    Promoted to a public name (from ``_sanitize_filename_part``) so ``internal_report.py`` can
    reuse it rather than duplicating it — see that module's docstring.
    """
    cleaned = "".join("-" if char in "/\\:" else char for char in value)
    cleaned = "_".join(cleaned.split())
    cleaned = cleaned.strip("._-")
    return cleaned or "unbenannt"


def to_ascii(value: str) -> str:
    """An ASCII-only rendering, for the latin-1-safe ``filename=`` fallback.

    HTTP header values must be latin-1 encodable and non-ASCII bytes in a plain ``filename=`` are
    interpreted inconsistently across browsers, so the umlaut-carrying name is carried by the
    RFC 5987 ``filename*`` parameter and this is only the fallback.
    """
    expanded = "".join(_ASCII_TRANSLITERATION.get(char, char) for char in value)
    decomposed = unicodedata.normalize("NFKD", expanded)
    return "".join(char for char in decomposed if char.isascii() and char.isprintable())


def attendance_list_filename(exam: Exam) -> str:
    """The German download filename, e.g. ``Anwesenheitsliste_WiSe_23-24_1._Termin.pdf``."""
    parts = [
        "Anwesenheitsliste",
        sanitize_filename_part(exam.semester),
        sanitize_filename_part(exam.termin),
    ]
    return "_".join(parts) + ".pdf"


def content_disposition(filename: str) -> str:
    """``Content-Disposition`` carrying both an ASCII fallback and the RFC 5987 UTF-8 name.

    The extension is split off generically (``.pdf`` or, for the §10/§11 Excel exports, ``.xlsx``)
    rather than a hardcoded ``.removesuffix(".pdf")``: the latter would leave an `.xlsx` filename's
    ASCII fallback ending in `...xlsx.pdf`. Every caller's filename has exactly one dot-extension,
    so a plain ``rsplit`` is exact here — no need for `pathlib`'s multi-suffix handling.
    """
    stem, _, extension = filename.rpartition(".")
    ascii_name = sanitize_filename_part(to_ascii(stem)) + "." + extension
    quoted = quote(filename, safe="")
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quoted}"
