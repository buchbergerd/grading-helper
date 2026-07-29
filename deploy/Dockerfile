# GradingHelper — production image skeleton.
#
# NOT YET FUNCTIONAL: backend/frontend app code doesn't exist yet (see /SPECIFICATION.md,
# /CLAUDE.md). This file fixes the shape the build must have so the no-runtime-network
# constraint (§13) is designed in from the start, not retrofitted:
#   - typst toolchain and fonts are baked in at build time.
#   - no `pip install` / `npm install` / typst package fetch may happen at container start.
#   - Typst's cetz/cetz-plot packages are downloaded and unpacked during the build, never at
#     runtime (see the vendoring step further down and docs/open-questions.md #17).
#
# TODO once app code exists: pin exact base image digests, fill in COPY paths, add a
# non-root user, healthcheck, and multi-stage layer caching for deps vs. app code.

# ---- frontend build stage ----
FROM node:22-slim AS frontend-build
WORKDIR /frontend
# COPY frontend/package.json frontend/package-lock.json ./
# RUN npm ci
# COPY frontend/ ./
# RUN npm run build

# ---- backend runtime ----
FROM python:3.12-slim AS backend

# Not cosmetic. The §9 internal report prints "Erstellt am: DD.MM.YYYY HH:MM" from the container's
# *local* time (app/statistics.py::_format_generated_at). A slim base image has no TZ set and
# therefore runs on UTC, which would print a German-facing timestamp one to two hours in the past
# — wrong in a way nobody would think to question. Set it here rather than in the app so the
# choice stays an operations decision; override it in docker-compose for another region.
ENV TZ=Europe/Berlin

# System deps: fonts for German umlauts (Noto Sans/Serif), typst binary.
# TODO: pin typst version; install from a release binary (no cargo/network at runtime).
RUN apt-get update && apt-get install -y --no-install-recommends \
        fonts-noto \
        fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/*

# TODO: install typst CLI binary (pinned version) into /usr/local/bin/typst here.

WORKDIR /app

# COPY backend/pyproject.toml backend/uv.lock ./
# RUN pip install --no-cache-dir uv && uv sync --frozen --no-dev

# COPY backend/app/ ./app/
# COPY backend/scripts/ ./scripts/
# COPY --from=frontend-build /frontend/dist ./static/

# Fetch cetz / cetz-plot (§12's charting packages for the §9 report) at BUILD time. This is the
# only step in the entire image that touches the network, and it lives here — on the build host —
# precisely so the running container never needs to (§13). The script pins both versions and
# verifies a SHA-256 before unpacking; `app/reports/internal_report.py` then points Typst at the
# resulting tree via `package_path`, so rendering a report never consults the @preview registry.
#
# Order matters: this must run AFTER the app source is copied, since the tree lands inside
# `app/reports/typst_packages/`. It is deliberately not committed to git (see /.gitignore) —
# it is reproducible from the pins in the script.
#
# Note this does not weaken §13. That constraint is about the deployed application never sending
# exam data (names, Matrikelnummern) anywhere; downloading a public package while building an
# image is a separate act, on a separate machine, involving no student data at all.
# RUN uv run python scripts/vendor_typst_packages.py

EXPOSE 8000
# CMD ["uv", "run", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
CMD ["echo", "GradingHelper image not yet implemented — see /SPECIFICATION.md"]
