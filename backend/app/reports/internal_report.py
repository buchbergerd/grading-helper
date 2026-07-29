"""The internal report (SPECIFICATION.md §9): a Typst PDF over the shared statistics payload.

Unlike :mod:`app.reports.attendance_list`, there is deliberately **no** ``build_*_data`` function
in this module. §9 requires the PDF and the interactive in-app dashboard to share "one backend
statistics-computation module so numbers are always consistent between them" — that module is
``app/statistics.py`` (:func:`~app.statistics.build_exam_statistics`), and it is the *only* place
that reads the ORM, decodes ``Decimal``s, rounds a percentage or builds a histogram bin label.
This module only turns the resulting :class:`~app.statistics.ExamStatistics` mapping into PDF
bytes. If a `build_*_data` step existed here too, the PDF and the dashboard would each compute
their own numbers from the ORM and could disagree — exactly the failure mode §9 names.

:func:`render_internal_report` therefore mirrors only the second half of
``attendance_list.py``'s split: it is a pure function of its argument, never touching the
database, and crosses the payload into ``templates/internal_report.typ`` as one JSON string via
Typst's ``sys.inputs``, same as the attendance list.

Rendering is offline by construction (§13): the template imports no ``@preview`` package (see its
own header comment for why — cetz/cetz-plot are a later, vendored milestone per §15.6), and
``ignore_system_fonts=True`` pins output to the fonts embedded in the typst binary itself.

The filename helpers (``sanitize_filename_part``, ``to_ascii``) and ``content_disposition`` are
imported from ``attendance_list.py`` rather than duplicated — that module promoted them from
private to public names for exactly this reuse.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

import typst

from app.models import Exam
from app.reports.attendance_list import content_disposition, sanitize_filename_part
from app.statistics import ExamStatistics

TEMPLATE_PATH = Path(__file__).parent / "templates" / "internal_report.typ"

__all__ = [
    "TEMPLATE_PATH",
    "content_disposition",
    "internal_report_filename",
    "render_internal_report",
]


@lru_cache(maxsize=1)
def _template_source() -> bytes:
    """The template, read once. It is a static file that ships with the package."""
    return TEMPLATE_PATH.read_bytes()


def render_internal_report(data: ExamStatistics) -> bytes:
    """Render the internal report to PDF bytes. Pure — no database, no request, no filesystem.

    An exam with nothing entered yet (zero registrations, no grading schema configured, empty
    histograms) renders a valid PDF stating plainly that nothing has been computed, rather than
    raising: §9 explicitly describes this as "a live view over current data... useful while
    grading is still in progress", so an early look at the report is a normal use case, not an
    error.
    """
    return typst.compile(
        _template_source(),
        sys_inputs={"data": json.dumps(data, ensure_ascii=False)},
        # §13: no outbound network calls at runtime. The template imports no @preview package, so
        # there is nothing to fetch; forbidding system fonts additionally guarantees the byte
        # output does not depend on which fonts the host happens to have installed.
        ignore_system_fonts=True,
    )


def internal_report_filename(exam: Exam) -> str:
    """The German download filename, e.g. ``Interner_Bericht_WiSe_23-24_1._Termin.pdf``."""
    parts = [
        "Interner_Bericht",
        sanitize_filename_part(exam.semester),
        sanitize_filename_part(exam.termin),
    ]
    return "_".join(parts) + ".pdf"
