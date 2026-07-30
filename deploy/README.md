# Deployment

Docker/compose artifacts for a real deployment. See `/docs/deployment.md` for the full runbook
(build, first boot, admin bootstrap, backups, reverse-proxy config) — this file only describes
what's in this directory. `/SPECIFICATION.md` §13 is the deployment requirements these satisfy.

## Files

- `Dockerfile` — multi-stage build: frontend build (Vite) → backend runtime (FastAPI), serving
  both from one container (the built frontend is copied into the image and served by
  `app/main.py`'s SPA-fallback route; there is no separate frontend container/origin). Python
  deps, the `typst` toolchain (the `typst` PyPI package is a self-contained compiled binary — no
  separate CLI install needed) and the vendored `cetz`/`cetz-plot` packages are all resolved at
  build time; nothing is fetched at container start.
- `docker-compose.yml` — single `app` service + a named volume for the SQLite file. No TLS here
  — assumes an existing department reverse proxy in front (confirmed setup steps in
  `/docs/deployment.md`).

## Not handled here, by design (§13)

- **SQLite backups**: explicitly deferred to department ops, not this app. `/docs/deployment.md`
  gives a WAL-safe backup command to build a cron job around.
- **TLS termination**: assumed to be an existing department reverse proxy in front of this
  container, which serves plain HTTP only.

## Deliberately deferred, not forgotten

- **Base-image digest pinning** (`python:3.12-slim`, `node:22-slim`): pinned by tag, not digest.
  A tag can move; a digest can't, at the cost of needing a manual bump whenever a security patch
  lands upstream. Worth doing once this is a real running deployment someone is responsible for
  patching — pin both `FROM` lines to `@sha256:...` and note the pinned date in the commit.
