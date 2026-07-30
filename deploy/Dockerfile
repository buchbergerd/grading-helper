# GradingHelper — production image.
#
# Two stages: the frontend compiles to static assets, then those land in the backend runtime
# image next to the FastAPI app. One container serves both the API (under /api) and the SPA
# (everything else — see app/main.py's spa_shell fallback), which is why the frontend build
# needs no separate origin/CORS story (frontend/vite.config.ts's dev proxy mirrors this).
#
# Self-contained at runtime (SPECIFICATION.md §13): Python deps, the typst toolchain (the
# `typst` PyPI package ships a statically-linked compiled binary — no separate CLI install
# needed, verified against this repo's dependency) and the vendored cetz/cetz-plot packages are
# all resolved at BUILD time. The only network access anywhere in this file is the
# vendor_typst_packages.py step below, and it never runs inside the started container.

# ---- frontend build stage ----
FROM node:22-slim AS frontend-build
WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# ---- backend runtime ----
FROM python:3.12-slim AS backend

# Not cosmetic. The §9 internal report prints "Erstellt am: DD.MM.YYYY HH:MM" from the
# container's *local* time (app/statistics.py::_format_generated_at). TZ alone is inert without
# the tzdata package on a slim base — Python silently falls back to UTC instead of erroring,
# which would print a German-facing timestamp one to two hours in the past and nobody would
# think to question it. Both lines are required together.
ENV TZ=Europe/Berlin \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH"

# No fonts-noto/system-fonts package here, deliberately: every app/reports/*.py render call sets
# `ignore_system_fonts=True`, so Typst never touches whatever fonts happen to be installed and
# renders with the fonts embedded in the typst binary itself — that's the whole point of the
# flag (byte-identical output regardless of host). Those embedded fonts cover German umlauts
# (ä/ö/ü/ß) fine; tests/test_attendance_list.py asserts real names ("Öztürk", "Straßer",
# "Müller") round-trip through a render via pdfplumber text extraction, which already proves
# this on the host and transfers directly to the container since system fonts are out of the
# picture either way. Installing fonts here would be dead weight the app never reads.
RUN apt-get update && apt-get install -y --no-install-recommends \
        tzdata \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv

WORKDIR /app

# Dependencies first, in their own layer, so an app-code-only change doesn't reinstall them.
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# backend/README.md is copied because pyproject.toml's `readme = "README.md"` makes hatchling
# read it as build metadata — omitting it fails the project install below, not just cosmetic.
COPY backend/README.md ./
COPY backend/app/ ./app/
COPY backend/scripts/ ./scripts/
RUN uv sync --frozen --no-dev

COPY --from=frontend-build /frontend/dist ./static/

# Fetch cetz / cetz-plot (§12) at BUILD time — the only network access in this whole image, and
# it happens on the build host, never inside the running container (§13). Pinned versions +
# SHA-256 verification live in the script itself; a re-published/compromised upstream archive
# fails the build loudly instead of silently landing in a report render.
#
# `--no-sync`: without it, `uv run` re-syncs the venv against pyproject.toml's *default*
# dependency groups before running anything — silently pulling mypy/ruff/pytest (the dev group)
# back into the image despite the `--no-dev` sync above, and re-touching the network to do it.
RUN uv run --no-sync python scripts/vendor_typst_packages.py

# Non-root. The image also creates /app/data here (rather than leaving it to the volume mount)
# so a *fresh* named volume inherits this ownership on first use — see docs/deployment.md for
# the extra step a bind-mounted data directory needs instead.
RUN useradd --system --create-home --uid 10001 gradinghelper \
    && mkdir -p /app/data \
    && chown -R gradinghelper:gradinghelper /app
USER gradinghelper

EXPOSE 8000

# Plain python3, not curl/wget: keeps the apt-get list above to tzdata only.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python3 -c "import urllib.request as u; u.urlopen('http://127.0.0.1:8000/api/health', timeout=3)" || exit 1

# Exec form, uvicorn as PID 1: it handles SIGTERM itself, so `docker stop`/compose shutdown is a
# clean graceful shutdown with no wrapper process needed. Deliberately not `uv run uvicorn ...`:
# `uv run` re-checks the lock on every start, which is a resolve attempt against the network on
# every container start — precisely what §13 forbids at runtime, not just at build time. The
# venv is on PATH (above) so plain `uvicorn` resolves to it directly.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips=*"]
