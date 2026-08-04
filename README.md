# GradingHelper

Internal tool for a university department to manage written-exam grading: import registered
students from the examination office's PDF export, record attendance and per-exercise points,
compute grades from a configurable grading schema, and generate the attendance list, internal
statistics report, examination-office report, and student results report (PDF/Excel).

All UI text and generated documents are in German; this repo's own docs are in English.

**Status: milestones 1–6 of `SPECIFICATION.md` §15 complete** — the full app, backend and
frontend: data model, auth/accounts, Lecture/Exam CRUD, registration-PDF import, the attendance
list, the grading engine, points/attendance entry, the §9 internal statistics report (PDF +
interactive dashboard), the §10/§11 examination-office and student-results reports, exam-settings
copy-forward, in-app exam deletion, and a real Docker deployment (`deploy/`, `docs/deployment.md`).
No open implementation work is tracked against §15 right now — see `docs/open-questions.md` for
anything that surfaces before adding new scope.

## Start here

- [`SPECIFICATION.md`](SPECIFICATION.md) — the full functional/technical spec. Source of truth
  for all behavior.
- [`CLAUDE.md`](CLAUDE.md) — for an implementing agent: repo layout, commands, and the specific
  invariants (Decimal arithmetic, collation, import validation, etc.) that are easy to get
  subtly wrong.
- [`docs/open-questions.md`](docs/open-questions.md) — tracked register of the spec's open
  assumptions/risks (§14) plus anything new found during implementation.
- [`docs/api-contract.md`](docs/api-contract.md) — the HTTP contract between backend and
  frontend, written before the code so the two sides agree without reading each other.
- [`docs/deployment.md`](docs/deployment.md) — the operator runbook: build, first boot, admin
  bootstrap, backups, reverse-proxy config.

## Stack

| Concern | Choice |
|---|---|
| Backend | Python + FastAPI |
| Frontend | React (+ Vite, TypeScript) |
| Database | SQLite |
| Registration-PDF parsing | `pdfplumber` (primary), `PyMuPDF` (fallback) |
| Report generation | Typst (`typst-py`), charts via `cetz`/`cetz-plot` (vendored) |
| Excel export | `openpyxl` |
| Deployment | Docker / docker-compose, behind an existing department reverse proxy |

See `SPECIFICATION.md` §12 for the full rationale behind each choice.

## Repo layout

```
.
├── SPECIFICATION.md   # source of truth for behavior
├── CLAUDE.md          # agent-facing invariants and pointers
├── backend/           # FastAPI app (see backend/README.md)
├── frontend/          # React app (see frontend/README.md)
├── deploy/            # real Dockerfile/compose for production (see deploy/README.md)
├── docs/              # open-questions register, ADRs as they accumulate
├── scripts/           # dev/test tooling, e.g. the synthetic PDF fixture generator
└── test_data/         # anonymized/synthetic registration-PDF fixtures only
```

## Running it

```
# backend (http://127.0.0.1:8000)
cd backend && uv sync && uv run uvicorn app.main:app --reload

# create the first admin account (prompts for the password; never pass it as an argument)
cd backend && uv run python scripts/create_admin.py --username <name>

# frontend (http://127.0.0.1:5173, proxies /api to the backend)
cd frontend && npm install && npm run dev
```

Tests: `cd backend && uv run pytest` and `cd frontend && npm run test`.

## Deploying

For a real department deployment (not local dev), see [`docs/deployment.md`](docs/deployment.md)
— it covers building/pulling the Docker image, first boot, admin bootstrap, backups, and
reverse-proxy configuration. Short version:

```
docker compose -f deploy/docker-compose.yml build
docker compose -f deploy/docker-compose.yml up -d
docker compose -f deploy/docker-compose.yml exec app python scripts/create_admin.py --username <name>
```

## Versioning

One [semantic version](https://semver.org/) (`MAJOR.MINOR.PATCH`) for the whole app — frontend
and backend ship as a single deployable unit (`deploy/Dockerfile`), not as independently
versioned packages. `backend/pyproject.toml` and `frontend/package.json` must carry the same
`version`; bump both together. The frontend footer displays its own copy, baked into the bundle
at build time from `frontend/package.json` (`frontend/vite.config.ts`'s `define`) — see
`frontend/src/components/Footer.tsx`.

Bump `MAJOR` for a breaking change to the deployed data (a migration an operator must plan
around) or to `docs/api-contract.md`, `MINOR` for a backward-compatible feature, `PATCH` for a
fix with no behavior change to the contract.

Bump in the same commit as the change it describes — don't batch several commits' worth of
change under one bump, and don't wait for a separate "release" commit; there isn't one, since
main is always deployable. Skip the bump for commits with no user-visible or contract effect
(docs, comments, test-only changes, dependency bumps with no behavior change).
`backend/tests/test_versioning.py` enforces that the two files agree; nothing enforces that a
given commit remembered to bump at all — that's on whoever (or whatever) is committing.

## Data sensitivity

Exam data includes real students' names and Matrikelnummern. Test fixtures in `test_data/`
must stay anonymized/synthetic (enforced by `.gitignore`, see `test_data/README.md`); never
commit a real registration-office export.
