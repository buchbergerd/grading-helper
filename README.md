# GradingHelper

Internal tool for a university department to manage written-exam grading: import registered
students from the examination office's PDF export, record attendance and per-exercise points,
compute grades from a configurable grading schema, and generate the attendance list, internal
statistics report, examination-office report, and student results report (PDF/Excel).

All UI text and generated documents are in German; this repo's own docs are in English.

**Status: milestone 1 of `SPECIFICATION.md` §15 complete** — data model, authentication and
accounts, Lecture/Exam CRUD, and a minimal React UI over them. Next up is §15.2: registration-PDF
import and the attendance list. Nothing in §5–§11 (PDF import, points entry, grading engine,
reports) exists yet.

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
├── deploy/            # Dockerfile/compose skeletons (see deploy/README.md)
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

## Data sensitivity

Exam data includes real students' names and Matrikelnummern. Test fixtures in `test_data/`
must stay anonymized/synthetic (enforced by `.gitignore`, see `test_data/README.md`); never
commit a real registration-office export.
