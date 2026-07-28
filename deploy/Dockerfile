# GradingHelper — production image skeleton.
#
# NOT YET FUNCTIONAL: backend/frontend app code doesn't exist yet (see /SPECIFICATION.md,
# /CLAUDE.md). This file fixes the shape the build must have so the no-runtime-network
# constraint (§13) is designed in from the start, not retrofitted:
#   - typst toolchain, fonts, and vendored cetz/cetz-plot packages are baked in at build time.
#   - no `pip install` / `npm install` / typst package fetch may happen at container start.
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

# Vendor cetz / cetz-plot Typst packages at build time (see vendor-typst-packages.sh) so
# report rendering never reaches out to the @preview registry at runtime.
# COPY deploy/typst-packages/ /root/.cache/typst/packages/preview/

# COPY backend/app/ ./app/
# COPY --from=frontend-build /frontend/dist ./static/

EXPOSE 8000
# CMD ["uv", "run", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
CMD ["echo", "GradingHelper image not yet implemented — see /SPECIFICATION.md"]
