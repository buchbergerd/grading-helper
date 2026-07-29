"""Generated reports (SPECIFICATION.md §6 and §9 today; §10-§11 land here later).

Ownership follows the same rule as every other exam-scoped route: authorise on
``Exam.owner_id`` via :func:`~app.api.exams.get_owned_exam`, which answers ``404`` — never
``403`` — for another instructor's exam, because a ``403`` would confirm that the exam exists.

The routes here return binary documents rather than JSON, so they declare
``response_class=Response`` and build the response by hand. No pydantic response model is defined
in this module: there is nothing JSON-shaped to describe. §6's "simple count shown in the UI
without generating the PDF" is a registration-listing concern and deliberately does not get a
competing endpoint here.
"""

from __future__ import annotations

from fastapi import APIRouter, Response

from app.api.exams import get_owned_exam
from app.auth.dependencies import CurrentUser, DbSession
from app.reports.attendance_list import (
    attendance_list_filename,
    build_attendance_list_data,
    content_disposition,
    render_attendance_list,
)
from app.reports.internal_report import internal_report_filename, render_internal_report
from app.statistics import build_exam_statistics

router = APIRouter(tags=["reports"])

PDF_MEDIA_TYPE = "application/pdf"


@router.get(
    "/exams/{exam_id}/reports/attendance-list",
    response_class=Response,
    responses={
        200: {
            "content": {PDF_MEDIA_TYPE: {}},
            "description": "Die Anwesenheitsliste als PDF.",
        }
    },
)
def attendance_list_report(exam_id: int, user: CurrentUser, db: DbSession) -> Response:
    """The exam's print-and-tick attendance list as a PDF (§6).

    Excluded students are omitted and the rows are German-collated in
    :func:`~app.reports.attendance_list.build_attendance_list_data`; this route only wires the
    ownership check to the renderer.

    An exam with no (non-excluded) registrations yields a valid PDF showing a head count of 0
    rather than an error — printing the sheet before importing the registration lists is a
    legitimate thing to do.

    Declared ``def`` rather than ``async def`` on purpose: the Typst compile is blocking CPU
    work, and FastAPI runs a sync route in a worker thread instead of stalling the event loop.
    """
    exam = get_owned_exam(db, user, exam_id)
    pdf = render_attendance_list(build_attendance_list_data(exam))
    return Response(
        content=pdf,
        media_type=PDF_MEDIA_TYPE,
        headers={
            "Content-Disposition": content_disposition(attendance_list_filename(exam)),
            # The document is a list of real names and Matrikelnummern (§13 treats these as
            # personal data) — keep it out of shared/proxy caches and browser back-button caches.
            "Cache-Control": "no-store",
        },
    )


@router.get(
    "/exams/{exam_id}/reports/internal",
    response_class=Response,
    responses={
        200: {
            "content": {PDF_MEDIA_TYPE: {}},
            "description": "Der interne Bericht als PDF.",
        }
    },
)
def internal_report(exam_id: int, user: CurrentUser, db: DbSession) -> Response:
    """The exam's internal statistics report as a PDF (§9).

    The statistics come from :func:`~app.statistics.build_exam_statistics` — the same call, and
    the same payload, that ``GET /exams/{id}/statistics`` serves to the dashboard. That is §9's
    "one backend statistics-computation module" requirement made literal: this route computes
    nothing itself, so the PDF cannot report a different number than the screen it was generated
    from.

    Unlike the §10/§11 exports, this is **not** gated by the §8.1 completeness check: §9 is a
    live view over grading in progress, and the report says on its face how many students are not
    yet included rather than refusing to render.

    ``def`` rather than ``async def``, as with the attendance list: the Typst compile is blocking
    CPU work and belongs in a worker thread.
    """
    exam = get_owned_exam(db, user, exam_id)
    pdf = render_internal_report(build_exam_statistics(exam))
    return Response(
        content=pdf,
        media_type=PDF_MEDIA_TYPE,
        headers={
            "Content-Disposition": content_disposition(internal_report_filename(exam)),
            # Aggregate statistics rather than a name list, but still an exam's grade
            # distribution and explicitly internal-only (§9) — same no-store posture.
            "Cache-Control": "no-store",
        },
    )
