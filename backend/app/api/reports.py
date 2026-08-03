"""Generated reports (SPECIFICATION.md §6, §9, §10, §11).

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

from fastapi import APIRouter, HTTPException, Response, status

from app.api.exams import get_owned_exam
from app.api.points import exam_completeness
from app.auth.dependencies import CurrentUser, DbSession
from app.grading.schema import GRADES
from app.models import Exam
from app.reports.attendance_list import (
    AttendanceListSortOrder,
    attendance_list_filename,
    build_attendance_list_data,
    content_disposition,
    render_attendance_list,
)
from app.reports.examination_office import (
    build_examination_office_data,
    examination_office_filename,
    render_examination_office_excel,
    render_examination_office_pdf,
)
from app.reports.internal_report import internal_report_filename, render_internal_report
from app.reports.student_results import (
    build_student_results_data,
    render_student_results_excel,
    render_student_results_pdf,
    student_results_filename,
)
from app.statistics import build_exam_statistics

router = APIRouter(tags=["reports"])

PDF_MEDIA_TYPE = "application/pdf"
EXCEL_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _require_exportable(exam: Exam) -> None:
    """The §10/§11 export gate: raise ``409`` with German error strings if either check fails.

    Two independent preconditions, both collected before raising so an instructor sees every
    blocker in one round trip rather than fixing one and hitting the next:

    * §8.1's completeness gate (:func:`~app.api.points.exam_completeness`) — every non-excluded
      student needs attendance recorded, and every attended student needs every exercise's points
      entered.
    * the grading schema must be fully configured — all ten §7.1 grades present — otherwise
      :func:`~app.reports.grades.student_note` has nothing to compute a grade against.

    The schema check duplicates ``app.api.points``'s/``app.statistics``'s two-line check rather
    than importing it: a core/report-adjacent module must not depend on a sibling API module for
    something this small (same reasoning ``app/statistics.py`` gives for its own copy).

    ``409``, not ``422``: this is "blocked by current server state" (an exam that is well-formed
    as a request but not yet ready to export), not a malformed request — same status
    ``app/api/exams.py::delete_exam`` uses for its confirm-required check.
    """
    errors: list[str] = []
    completeness = exam_completeness(exam)
    if not completeness.is_complete:
        errors.append(
            f"{completeness.incomplete_count} Anmeldung(en) sind noch unvollständig "
            "(Anwesenheit oder Punkte fehlen)."
        )
    if len(exam.grade_thresholds) != len(GRADES):
        errors.append("Das Notenschema ist noch nicht vollständig konfiguriert.")
    if errors:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail={"errors": errors})


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
def attendance_list_report(
    exam_id: int,
    user: CurrentUser,
    db: DbSession,
    sort_order: AttendanceListSortOrder = AttendanceListSortOrder.COURSE_NACHNAME,
) -> Response:
    """The exam's print-and-tick attendance list as a PDF (§6).

    Excluded students are omitted and the rows are German-collated in
    :func:`~app.reports.attendance_list.build_attendance_list_data`; this route only wires the
    ownership check to the renderer.

    ``sort_order`` picks one of the four printable orders (§6's course-then-Nachname default plus
    Nachname-only, Matrikelnummer-only and course-then-Matrikelnummer); an unknown value is
    rejected with ``422`` by FastAPI's own enum validation, no explicit check needed here.

    An exam with no (non-excluded) registrations yields a valid PDF showing a head count of 0
    rather than an error — printing the sheet before importing the registration lists is a
    legitimate thing to do.

    Declared ``def`` rather than ``async def`` on purpose: the Typst compile is blocking CPU
    work, and FastAPI runs a sync route in a worker thread instead of stalling the event loop.
    """
    exam = get_owned_exam(db, user, exam_id)
    pdf = render_attendance_list(build_attendance_list_data(exam, sort_order=sort_order))
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


@router.get(
    "/exams/{exam_id}/reports/examination-office/pdf",
    response_class=Response,
    responses={
        200: {
            "content": {PDF_MEDIA_TYPE: {}},
            "description": "Der Prüfungsamt-Bericht als PDF.",
        },
        409: {"description": "Die Prüfung ist noch nicht exportbereit (§8.1)."},
    },
)
def examination_office_pdf_report(exam_id: int, user: CurrentUser, db: DbSession) -> Response:
    """The exam's examination-office report as a PDF (§10).

    Blocked by :func:`_require_exportable` (§8.1 completeness plus a fully configured grading
    schema) before any data is built — an incomplete or unconfigured exam never reaches
    :func:`~app.reports.examination_office.build_examination_office_data`, which assumes both
    preconditions already hold.

    ``def`` rather than ``async def``: the Typst compile is blocking CPU work and belongs in a
    worker thread, same as the attendance list and internal report routes.
    """
    exam = get_owned_exam(db, user, exam_id)
    _require_exportable(exam)
    pdf = render_examination_office_pdf(build_examination_office_data(exam))
    return Response(
        content=pdf,
        media_type=PDF_MEDIA_TYPE,
        headers={
            "Content-Disposition": content_disposition(
                examination_office_filename(exam, extension="pdf")
            ),
            # Names and Matrikelnummern (§13 personal data) — keep out of shared/proxy caches.
            "Cache-Control": "no-store",
        },
    )


@router.get(
    "/exams/{exam_id}/reports/examination-office/excel",
    response_class=Response,
    responses={
        200: {
            "content": {EXCEL_MEDIA_TYPE: {}},
            "description": "Der Prüfungsamt-Bericht als Excel-Datei.",
        },
        409: {"description": "Die Prüfung ist noch nicht exportbereit (§8.1)."},
    },
)
def examination_office_excel_report(exam_id: int, user: CurrentUser, db: DbSession) -> Response:
    """The exam's examination-office report as an .xlsx workbook (§10). Same gate as the PDF."""
    exam = get_owned_exam(db, user, exam_id)
    _require_exportable(exam)
    workbook = render_examination_office_excel(build_examination_office_data(exam))
    return Response(
        content=workbook,
        media_type=EXCEL_MEDIA_TYPE,
        headers={
            "Content-Disposition": content_disposition(
                examination_office_filename(exam, extension="xlsx")
            ),
            "Cache-Control": "no-store",
        },
    )


@router.get(
    "/exams/{exam_id}/reports/student-results/pdf",
    response_class=Response,
    responses={
        200: {
            "content": {PDF_MEDIA_TYPE: {}},
            "description": "Die Notenliste als PDF.",
        },
        409: {"description": "Die Prüfung ist noch nicht exportbereit (§8.1)."},
    },
)
def student_results_pdf_report(exam_id: int, user: CurrentUser, db: DbSession) -> Response:
    """The exam's student-results report as a PDF (§11).

    Blocked by :func:`_require_exportable` (§8.1 completeness plus a fully configured grading
    schema) before any data is built — an incomplete or unconfigured exam never reaches
    :func:`~app.reports.student_results.build_student_results_data`, which assumes both
    preconditions already hold.

    ``def`` rather than ``async def``: the Typst compile is blocking CPU work and belongs in a
    worker thread, same as the other report routes.
    """
    exam = get_owned_exam(db, user, exam_id)
    _require_exportable(exam)
    pdf = render_student_results_pdf(build_student_results_data(exam))
    return Response(
        content=pdf,
        media_type=PDF_MEDIA_TYPE,
        headers={
            "Content-Disposition": content_disposition(
                student_results_filename(exam, extension="pdf")
            ),
            # Matrikelnummern (§13 personal data) — keep out of shared/proxy caches.
            "Cache-Control": "no-store",
        },
    )


@router.get(
    "/exams/{exam_id}/reports/student-results/excel",
    response_class=Response,
    responses={
        200: {
            "content": {EXCEL_MEDIA_TYPE: {}},
            "description": "Die Notenliste als Excel-Datei.",
        },
        409: {"description": "Die Prüfung ist noch nicht exportbereit (§8.1)."},
    },
)
def student_results_excel_report(exam_id: int, user: CurrentUser, db: DbSession) -> Response:
    """The exam's student-results report as an .xlsx workbook (§11). Same gate as the PDF."""
    exam = get_owned_exam(db, user, exam_id)
    _require_exportable(exam)
    workbook = render_student_results_excel(build_student_results_data(exam))
    return Response(
        content=workbook,
        media_type=EXCEL_MEDIA_TYPE,
        headers={
            "Content-Disposition": content_disposition(
                student_results_filename(exam, extension="xlsx")
            ),
            "Cache-Control": "no-store",
        },
    )
