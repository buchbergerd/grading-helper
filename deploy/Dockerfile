# GradingHelper — production image skeleton.
#
# NOT YET FUNCTIONAL: backend/frontend app code doesn't exist yet (see /SPECIFICATION.md,
# /CLAUDE.md). This file fixes the shape the build must have so the no-runtime-network
# constraint (§13) is designed in from the start, not retrofitted:
#   - typst toolchain and fonts are baked in at build time.
#   - no `pip install` / `npm install` / typst package fetch may happen at container start.
#   - no Typst @preview package is imported by any template, so none needs vendoring today
#     (see the note further down and docs/open-questions.md #17).
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

# Vendoring cetz / cetz-plot is currently NOT NEEDED — no template imports an @preview package.
# §12 named them for charts, but §9's report draws its bars with native Typst primitives behind
# one `bar-chart` function (see app/reports/templates/internal_report.typ and open-questions #17),
# so there is nothing to fetch and the §13 no-runtime-network constraint holds trivially. Restore
# the two lines below only if a later report adopts a package:
# COPY deploy/typst-packages/ /root/.cache/typst/packages/preview/
# (typst-py accepts `package_path`/`package_cache_path`, so a vendored tree is wired up in Python,
# not via an env var.)

# COPY backend/app/ ./app/
# COPY --from=frontend-build /frontend/dist ./static/

EXPOSE 8000
# CMD ["uv", "run", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
CMD ["echo", "GradingHelper image not yet implemented — see /SPECIFICATION.md"]
