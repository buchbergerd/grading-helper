"""FastAPI application entry point.

Run with ``uv run uvicorn app.main:app --reload`` from ``backend/``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse

from app.api import (
    admin,
    auth,
    exams,
    lectures,
    points,
    registrations,
    reports,
    sharing,
    statistics,
)
from app.migrations import run_migrations


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Bring the database schema up to date on startup (``alembic upgrade head``).

    Done in the lifespan hook rather than at import time so that merely importing this module
    (tests, `--help`, tooling) never touches the filesystem. Runs against an empty database same
    as an existing one — see ``app/migrations.py``.
    """
    run_migrations()
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
# Exam-scoped generated documents: ``/api/exams/{id}/reports/attendance-list`` (§6) and
# ``/api/exams/{id}/reports/internal`` (§9).
app.include_router(reports.router, prefix="/api")
# The same §9 statistics as JSON for the live dashboard: ``/api/exams/{id}/statistics``. Separate
# from the reports router, which serves only binary documents.
app.include_router(statistics.router, prefix="/api")
# §3's second public-access exception: owner-only share-link management
# (``/api/exams/{id}/share-link``) plus the one unauthenticated route this app has besides
# ``/health`` and auth, ``/api/public/statistics/{token}``.
app.include_router(sharing.router, prefix="/api")


# The built frontend (``npm run build``), copied to ``/app/static`` by ``deploy/Dockerfile`` so
# one container serves both the API and the SPA (§13: a single self-contained image). Absent in
# local dev (Vite's own dev server proxies /api instead, see frontend/vite.config.ts) and in
# tests, so this mounts conditionally rather than failing to start.
_STATIC_DIR = Path(__file__).resolve().parent.parent / "static"

if _STATIC_DIR.is_dir():

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa_shell(full_path: str) -> FileResponse:
        """Serve the built frontend, falling back to ``index.html`` for any path React
        Router (BrowserRouter) owns client-side — e.g. a hard refresh on ``/lectures/5``
        must return the SPA shell, not a 404.

        Registered after every ``/api`` router above, so those are matched first and never
        reach this handler. An unmatched ``/api/...`` path is guarded explicitly below —
        without it, a typo'd API route would silently 200 with the HTML shell instead of a
        clean 404.
        """
        if full_path == "api" or full_path.startswith("api/"):
            raise HTTPException(status_code=404)
        candidate = _STATIC_DIR / full_path
        if full_path and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(_STATIC_DIR / "index.html")
