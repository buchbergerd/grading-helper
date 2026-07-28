"""FastAPI application entry point.

Run with ``uv run uvicorn app.main:app --reload`` from ``backend/``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api import admin, auth, exams, lectures, points, registrations, reports
from app.db import init_db


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Create the database schema on startup.

    Done in the lifespan hook rather than at import time so that merely importing this module
    (tests, `--help`, tooling) never touches the filesystem.
    """
    init_db()
    yield


app = FastAPI(title="GradingHelper", lifespan=lifespan)


@app.get("/api/health")
async def health() -> dict[str, str]:
    """Liveness probe for the container/reverse proxy (§13). Deliberately unauthenticated."""
    return {"status": "ok"}


# Routers are registered here as the milestones land. Still to come: registration import
# (§15.2), points entry (§15.3), the remaining reports (§15.4-§15.5).
app.include_router(auth.router, prefix="/api")
app.include_router(admin.router, prefix="/api")
app.include_router(lectures.router, prefix="/api")
# The exams router carries no prefix of its own: it owns both ``/api/exams/...`` and the
# nested creation route ``/api/lectures/{id}/exams`` (contract: Exams).
app.include_router(exams.router, prefix="/api")
# Same reasoning: this router owns both ``/api/exams/{id}/registrations...`` (§5.3 import and
# list) and the flat ``/api/registrations/{id}`` edit/delete routes.
app.include_router(registrations.router, prefix="/api")
# Points/attendance entry and the §8.1 completeness gate (§8): ``/api/exams/{id}/points``,
# ``/api/exams/{id}/completeness`` and the flat ``/api/registrations/{id}/points``.
app.include_router(points.router, prefix="/api")
# Exam-scoped generated documents, e.g. ``/api/exams/{id}/reports/attendance-list`` (§6).
app.include_router(reports.router, prefix="/api")
