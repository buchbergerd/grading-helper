"""FastAPI application entry point.

Run with ``uv run uvicorn app.main:app --reload`` from ``backend/``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api import admin, auth, exams, lectures
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
# (§15.2), points entry (§15.3), reports (§15.4-§15.5).
app.include_router(auth.router, prefix="/api")
app.include_router(admin.router, prefix="/api")
app.include_router(lectures.router, prefix="/api")
# The exams router carries no prefix of its own: it owns both ``/api/exams/...`` and the
# nested creation route ``/api/lectures/{id}/exams`` (contract: Exams).
app.include_router(exams.router, prefix="/api")
