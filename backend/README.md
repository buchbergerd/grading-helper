# GradingHelper backend

FastAPI service — see `/SPECIFICATION.md` (repo root) for the full spec and `/CLAUDE.md` for the
invariants an implementation must not violate. All of §15's milestones 1–6 are implemented; see
the repo-root `README.md` and `CLAUDE.md` for current status.

## Layout

```
backend/
├── app/
│   ├── main.py              # FastAPI app instance, router registration, migrations on startup
│   ├── models/               # SQLAlchemy models (User, Lecture, Exam, StudentRegistration, ...)
│   ├── api/                  # route modules, one per resource
│   ├── auth/                 # password hashing, session/token handling (§3)
│   ├── grading/               # §7 grade computation engine — pure functions, Decimal only
│   ├── pdf_import/            # §5 registration-PDF parsing (pdfplumber primary, PyMuPDF fallback)
│   ├── statistics.py          # §9 — the only module that turns an exam into statistics
│   ├── collation.py           # §6 DIN 5007-1 German name sort
│   ├── migrations.py          # runs `alembic upgrade head` (production's schema path)
│   └── reports/
│       ├── templates/         # Typst (.typ) templates for attendance list, internal/office/student reports
│       ├── typst_packages/    # vendored cetz/cetz-plot (gitignored, see scripts/vendor_typst_packages.py)
│       └── ...                # attendance_list.py, internal_report.py, examination_office.py, student_results.py
├── alembic/                  # schema migrations (versions/, env.py)
├── tests/
│   ├── test_fixtures_are_wellformed.py  # guards the synthetic PDFs in /test_data (see scripts/make_fixtures.py)
│   ├── test_migrations.py    # guards alembic and Base.metadata staying in sync
│   └── ...
└── pyproject.toml
```

## Setup

This project uses [uv](https://docs.astral.sh/uv/) (or plain `pip`) against `pyproject.toml`.

```
uv sync
uv run pytest
uv run uvicorn app.main:app --reload
```

## Dev-server default accounts

The local dev/test SQLite database (`backend/data/gradinghelper.db`) currently has these
accounts for manual testing. These are **not** created by any script or migration — they exist
only in this developer's local DB and must never be assumed present elsewhere (a fresh DB has no
users until `scripts/create_admin.py` is run). Do not reuse this password scheme on a real
deployment.

| username | password | admin |
|----------|----------|-------|
| `admin`  | `12345678` | yes |
| `test`   | `12345678` | no |

## Non-negotiable constraints

See `/CLAUDE.md` — in particular: `Decimal` end-to-end for all points/percentage/threshold
arithmetic (never `float`, never SQLite `REAL`), and the §5.3 import row-count checksum must
hard-fail rather than silently import a partial student list.
