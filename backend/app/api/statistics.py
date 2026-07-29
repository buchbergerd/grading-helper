"""The §9 internal-report statistics endpoint.

Split out from ``app/api/reports.py`` because that module is deliberately about *binary
documents* and declares no JSON shapes. This route serves the same §9 statistics as JSON, for the
interactive dashboard.

Both consumers read one payload from one producer — ``app.statistics.build_exam_statistics`` —
which is §9's explicit requirement ("sharing one backend statistics-computation module so numbers
are always consistent between them"). This route adds nothing on top of it: no filtering, no
reshaping, no rounding. If a number needs to change, it changes in ``app/statistics.py`` and both
views move together.

Two things this route deliberately does **not** do:

*It does not apply the §8.1 completeness gate.* That gate belongs to the §10/§11 exports only.
§9 is "a live view over current data, not a static export — it reflects entered points
immediately, useful while grading is still in progress", so an exam that is half-graded gets a
``200`` describing how much is still missing, never a ``409``.

*It does not offer an admin view.* Authorisation is :func:`~app.api.exams.get_owned_exam`, so an
administrator asking for another instructor's exam gets the same ``404`` as any other
non-owner — §3's least-privilege default, restated in §9 ("visible only to the exam's owner
(and, per §3, not to admins by default)").
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.exams import get_owned_exam
from app.auth.dependencies import CurrentUser, DbSession
from app.statistics import ExamStatistics, build_exam_statistics

router = APIRouter(tags=["statistics"])


@router.get("/exams/{exam_id}/statistics", response_model=None)
def exam_statistics(exam_id: int, user: CurrentUser, db: DbSession) -> ExamStatistics:
    """The exam's §9 statistics, live.

    ``response_model=None`` is deliberate. :class:`~app.statistics.ExamStatistics` is already
    exactly the wire shape — every decimal in it is a canonical string, by construction — so
    routing it through a pydantic model would buy no safety and cost some: pydantic's lax mode
    would happily coerce a stray ``float`` into a string on the way out, converting a §7.0
    violation into a silently plausible response. The dict is returned as-is; ``app/statistics.py``
    is where the shape is enforced, and its tests assert that no ``float`` appears anywhere in the
    payload.

    Declared ``def`` rather than ``async def``: the computation is blocking CPU work over the
    exam's registrations, so FastAPI should run it in a worker thread rather than on the event
    loop.
    """
    exam = get_owned_exam(db, user, exam_id)
    return build_exam_statistics(exam)
