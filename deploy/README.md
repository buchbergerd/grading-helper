# Deployment

Skeletons only — fill in once backend/frontend exist. See `/SPECIFICATION.md` §13 for the
deployment requirements this must satisfy.

## Files

- `Dockerfile` — multi-stage build (frontend build → backend runtime with typst + fonts baked
  in). Marked with `TODO`s where real COPY/RUN steps go once there's app code to build.
- `docker-compose.yml` — single `app` service + a named volume for the SQLite file. No TLS
  here — assumes an existing department reverse proxy in front (confirm with department IT
  before going live, per §13).

## Outstanding before this is real

1. **Vendor `cetz` / `cetz-plot`** into the image at build time (pinned versions), so Typst
   report rendering never fetches from the `@preview` registry at runtime. Needs a
   `vendor-typst-packages.sh` (or equivalent) that downloads them once at image-build time and
   copies them into Typst's package cache path inside the image — not at container start.
2. Pin a specific `typst` CLI release and install it as a binary in the image.
3. Decide and document the SQLite backup approach (cron + `sqlite3 .backup`, Litestream, or
   similar) — explicitly deferred to department ops per §13, but the compose volume above is
   where it needs to attach.
4. Non-root user, healthcheck, pinned base-image digests.
