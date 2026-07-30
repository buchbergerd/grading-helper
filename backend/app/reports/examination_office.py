"""The examination-office report (SPECIFICATION.md §10) — PDF and Excel.

This is one of two sibling §8.1-gated exports; §11's "student results" report
(``app/reports/student_results.py``) copies this module's shape. Keep this a clean template
rather than a one-off.

Same split as ``attendance_list.py``: ``build_examination_office_data(exam)`` is a pure ORM →
JSON-serialisable function (no database writes, no HTTP, no rendering), and
``render_examination_office_pdf``/``render_examination_office_excel`` are pure functions of that
payload. The route (``app/api/reports.py``) is the only place that runs the §8.1 completeness gate
and the grading-schema check — this module assumes both already passed (see
:func:`build_examination_office_data`'s docstring) and fails loudly, not silently, if that
precondition is violated.

**Grouping key is the pair ``(course_code, module_title)``, not ``course_code`` alone.** §10 says
the section heading is the course's full ``module_title`` — the verbatim per-course-PDF text
(CLAUDE.md, §4/§5.1) — and a Kombinationsprüfung can legitimately have two course PDFs sharing a
``course_code`` with different ``module_title``s (different BPO version, different credit points).
Grouping on ``course_code`` alone would silently merge those into one section and show a student
under a module heading they were never registered under.
"""

from __future__ import annotations

import io
import json
from functools import lru_cache
from pathlib import Path
from typing import TypedDict

import typst
from openpyxl import Workbook  # type: ignore[import-untyped]
from openpyxl.styles import Font  # type: ignore[import-untyped]

from app.collation import german_sort_key
from app.models import Exam, StudentRegistration
from app.reports import grades
from app.reports.attendance_list import (
    content_disposition,
    format_german_date,
    sanitize_filename_part,
)

__all__ = [
    "ExaminationOfficeData",
    "ExaminationOfficeRow",
    "ExaminationOfficeSection",
    "build_examination_office_data",
    "content_disposition",
    "examination_office_filename",
    "render_examination_office_excel",
    "render_examination_office_pdf",
]

TEMPLATE_PATH = Path(__file__).parent / "templates" / "examination_office.typ"


class ExaminationOfficeRow(TypedDict):
    """One printed row — §10's four columns, in §10's order."""

    matrikelnummer: str
    nachname: str
    vorname: str
    note: str


class ExaminationOfficeSection(TypedDict):
    """One course's block: its full ``module_title`` heading plus its sorted rows."""

    course_code: str
    module_title: str
    rows: list[ExaminationOfficeRow]


class ExaminationOfficeData(TypedDict):
    """Everything the renderers need. JSON-serialisable by construction."""

    lecture_name: str
    semester: str
    termin: str
    exam_date: str | None
    sections: list[ExaminationOfficeSection]


def build_examination_office_data(exam: Exam) -> ExaminationOfficeData:
    """The §10 payload for one exam.

    **Precondition, not re-validated here**: the caller (the report route) must already have run
    the §8.1 completeness gate and confirmed the grading schema is fully configured. This function
    assumes both — it is only ever reachable via a caller bug if either is false, so a loud
    :class:`RuntimeError` here (from :func:`~app.reports.grades.thresholds_or_none` returning
    ``None``) is correct, not a silent ``"n.e."``/``500``.

    Excluded registrations are omitted (§5.3). Sections are grouped by ``(course_code,
    module_title)`` — see the module docstring — and sorted with
    :func:`~app.collation.german_sort_key` on that pair, mirroring how
    ``attendance_list.py`` German-collates its per-course header block. Rows within a section are
    sorted by ``matrikelnummer`` as a plain string, the same convention
    ``app.api.points.exam_completeness``/``read_points_grid`` already use for Matrikelnummer
    ordering (opaque digit string, not a number).
    """
    thresholds = grades.thresholds_or_none(exam)
    if thresholds is None:
        raise RuntimeError(
            "build_examination_office_data called with an incompletely configured grading "
            "schema; the caller must verify the schema is complete before calling this function."
        )
    max_points = grades.total_max_points(exam)
    exercise_ids = [exercise.id for exercise in exam.exercises]

    groups: dict[tuple[str, str], list[StudentRegistration]] = {}
    for registration in exam.registrations:
        if registration.excluded:
            continue
        key = (registration.course_code, registration.module_title)
        groups.setdefault(key, []).append(registration)

    sections: list[ExaminationOfficeSection] = []
    for course_code, module_title in sorted(
        groups, key=lambda pair: (german_sort_key(pair[0]), german_sort_key(pair[1]))
    ):
        registrations = sorted(groups[(course_code, module_title)], key=lambda r: r.matrikelnummer)
        rows = [
            ExaminationOfficeRow(
                matrikelnummer=registration.matrikelnummer,
                nachname=registration.nachname,
                vorname=registration.vorname,
                note=grades.student_note(
                    registration,
                    exercise_ids=exercise_ids,
                    thresholds=thresholds,
                    max_points=max_points,
                    bonus_mode=exam.bonus_mode,
                ),
            )
            for registration in registrations
        ]
        sections.append(
            ExaminationOfficeSection(course_code=course_code, module_title=module_title, rows=rows)
        )

    return ExaminationOfficeData(
        lecture_name=exam.lecture.name,
        semester=exam.semester,
        termin=exam.termin,
        exam_date=format_german_date(exam.exam_date),
        sections=sections,
    )


@lru_cache(maxsize=1)
def _template_source() -> bytes:
    """The template, read once. It is a static file that ships with the package."""
    return TEMPLATE_PATH.read_bytes()


def render_examination_office_pdf(data: ExaminationOfficeData) -> bytes:
    """Render the examination-office report to PDF bytes. Pure — no database, no request.

    Zero sections (an exam with no non-excluded registrations) renders a valid PDF stating
    plainly that no students are registered, rather than raising — same posture as
    ``attendance_list.py``'s zero-registrations case.
    """
    return typst.compile(
        _template_source(),
        sys_inputs={"data": json.dumps(data, ensure_ascii=False)},
        # §13: no outbound network calls at runtime. No @preview import (no charts needed), and
        # forbidding system fonts pins output to the fonts embedded in the typst binary itself.
        ignore_system_fonts=True,
    )


def render_examination_office_excel(data: ExaminationOfficeData) -> bytes:
    """Render the examination-office report to an .xlsx workbook. Pure — no database, no request.

    One flat worksheet (§10: "kept deliberately simple"), not one sheet per section — a flat sheet
    loses the PDF's visual section grouping, which §10 explicitly says to compensate for with an
    extra ``Modultitel`` column rather than by mirroring the PDF's structure in the workbook.

    Every cell is written as a Python ``str``: ``Matr.-Nr.`` written as a number would lose a
    leading zero and right-align; ``Note`` is mixed numeric/text ("1,3" / "nicht bestanden" /
    "n.e.") anyway.
    """
    workbook = Workbook()
    sheet = workbook.active
    assert sheet is not None
    sheet.title = "Prüfungsamt"

    headers = ["Matr.-Nr.", "Nachname", "Vorname", "Note", "Modultitel"]
    sheet.append(headers)
    for cell in sheet[1]:
        cell.font = Font(bold=True)

    for section in data["sections"]:
        for row in section["rows"]:
            sheet.append(
                [
                    str(row["matrikelnummer"]),
                    str(row["nachname"]),
                    str(row["vorname"]),
                    str(row["note"]),
                    str(section["module_title"]),
                ]
            )

    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def examination_office_filename(exam: Exam, *, extension: str) -> str:
    """The German download filename, e.g. ``Pruefungsamt_WiSe_23-24_1._Termin.pdf``."""
    parts = [
        "Pruefungsamt",
        sanitize_filename_part(exam.semester),
        sanitize_filename_part(exam.termin),
    ]
    return "_".join(parts) + "." + extension
