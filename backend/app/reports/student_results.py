"""The student-results report (SPECIFICATION.md §11) — PDF and Excel.

The simpler sibling of ``examination_office.py`` (§10): no course/module grouping, no names —
just Matrikelnummer and Note, one flat list sorted by Matrikelnummer. §11 explicitly frames this
as "matches common practice of posting anonymized grade lists", so the absence of names is a
privacy property this module (and its tests) must preserve deliberately, not an oversight.

Same split as ``attendance_list.py``/``examination_office.py``:
``build_student_results_data(exam)`` is a pure ORM → JSON-serialisable function (no database
writes, no HTTP, no rendering), and ``render_student_results_pdf``/``render_student_results_excel``
are pure functions of that payload. The route (``app/api/reports.py``) is the only place that runs
the §8.1 completeness gate and the grading-schema check — this module assumes both already passed
(see :func:`build_student_results_data`'s docstring) and fails loudly, not silently, if that
precondition is violated.
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

from app.models import Exam
from app.reports import grades
from app.reports.attendance_list import (
    content_disposition,
    format_german_date,
    sanitize_filename_part,
)

__all__ = [
    "StudentResultRow",
    "StudentResultsData",
    "build_student_results_data",
    "content_disposition",
    "render_student_results_excel",
    "render_student_results_pdf",
    "student_results_filename",
]

TEMPLATE_PATH = Path(__file__).parent / "templates" / "student_results.typ"


class StudentResultRow(TypedDict):
    """One printed row — §11's two columns, in §11's order."""

    matrikelnummer: str
    note: str


class StudentResultsData(TypedDict):
    """Everything the renderers need. JSON-serialisable by construction."""

    lecture_name: str
    semester: str
    termin: str
    exam_date: str | None
    rows: list[StudentResultRow]


def build_student_results_data(exam: Exam) -> StudentResultsData:
    """The §11 payload for one exam.

    **Precondition, not re-validated here**: the caller (the report route) must already have run
    the §8.1 completeness gate and confirmed the grading schema is fully configured. This function
    assumes both — it is only ever reachable via a caller bug if either is false, so a loud
    :class:`RuntimeError` here (from :func:`~app.reports.grades.thresholds_or_none` returning
    ``None``) is correct, not a silent ``"n.e."``/``500``.

    Excluded registrations are omitted (§5.3). **No course/module grouping at all** (§11: "Sort:
    by Matrikelnummer only (no course grouping)") — one flat list, sorted by ``matrikelnummer`` as
    a plain string, the same convention ``app.api.points.exam_completeness``/``read_points_grid``
    and ``examination_office.py`` already use for Matrikelnummer ordering (opaque digit string,
    not a number).
    """
    thresholds = grades.thresholds_or_none(exam)
    if thresholds is None:
        raise RuntimeError(
            "build_student_results_data called with an incompletely configured grading "
            "schema; the caller must verify the schema is complete before calling this function."
        )
    max_points = grades.total_max_points(exam)
    exercise_ids = [exercise.id for exercise in exam.exercises]

    registrations = sorted(
        (registration for registration in exam.registrations if not registration.excluded),
        key=lambda r: r.matrikelnummer,
    )
    rows = [
        StudentResultRow(
            matrikelnummer=registration.matrikelnummer,
            note=grades.student_note(
                registration,
                exercise_ids=exercise_ids,
                thresholds=thresholds,
                max_points=max_points,
                bonus_mode=exam.bonus_mode,
                bonus_points=exam.bonus_points,
            ),
        )
        for registration in registrations
    ]

    return StudentResultsData(
        lecture_name=exam.lecture.name,
        semester=exam.semester,
        termin=exam.termin,
        exam_date=format_german_date(exam.exam_date),
        rows=rows,
    )


@lru_cache(maxsize=1)
def _template_source() -> bytes:
    """The template, read once. It is a static file that ships with the package."""
    return TEMPLATE_PATH.read_bytes()


def render_student_results_pdf(data: StudentResultsData) -> bytes:
    """Render the student-results report to PDF bytes. Pure — no database, no request.

    Zero rows (an exam with no non-excluded registrations) renders a valid PDF stating plainly
    that no students are registered, rather than raising — same posture as
    ``examination_office.py``'s zero-sections case.
    """
    return typst.compile(
        _template_source(),
        sys_inputs={"data": json.dumps(data, ensure_ascii=False)},
        # §13: no outbound network calls at runtime. No @preview import (no charts needed), and
        # forbidding system fonts pins output to the fonts embedded in the typst binary itself.
        ignore_system_fonts=True,
    )


def render_student_results_excel(data: StudentResultsData) -> bytes:
    """Render the student-results report to an .xlsx workbook. Pure — no database, no request.

    One flat worksheet, two columns. Every cell is written as a Python ``str``: ``Matr.-Nr.``
    written as a number would lose a leading zero and right-align; ``Note`` is mixed
    numeric/text ("1,3" / "nicht bestanden" / "n.e.") anyway.
    """
    workbook = Workbook()
    sheet = workbook.active
    assert sheet is not None
    sheet.title = "Notenliste"

    headers = ["Matr.-Nr.", "Note"]
    sheet.append(headers)
    for cell in sheet[1]:
        cell.font = Font(bold=True)

    for row in data["rows"]:
        sheet.append([str(row["matrikelnummer"]), str(row["note"])])

    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def student_results_filename(exam: Exam, *, extension: str) -> str:
    """The German download filename, e.g. ``Notenliste_WiSe_23-24_1._Termin.pdf``."""
    parts = [
        "Notenliste",
        sanitize_filename_part(exam.semester),
        sanitize_filename_part(exam.termin),
    ]
    return "_".join(parts) + "." + extension
