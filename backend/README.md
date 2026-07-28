# GradingHelper backend

FastAPI service. Not implemented yet — see `/SPECIFICATION.md` (repo root) for the full spec and
`/CLAUDE.md` for the invariants an implementation must not violate.

## Intended layout

```
backend/
├── app/
│   ├── main.py              # FastAPI app instance, router registration
│   ├── models/               # SQLAlchemy models (User, Lecture, Exam, StudentRegistration, ...)
│   ├── api/                  # route modules, one per resource
│   ├── auth/                 # password hashing, session/token handling (§3)
│   ├── grading/               # §7 grade computation engine — pure functions, Decimal only
│   ├── pdf_import/            # §5 registration-PDF parsing (pdfplumber primary, PyMuPDF fallback)
│   └── reports/
│       ├── templates/         # Typst (.typ) templates for attendance list, internal/office/student reports
│       └── ...                # statistics module shared by internal-report PDF + dashboard (§9)
├── tests/
│   ├── test_fixtures_are_wellformed.py  # guards the synthetic PDFs in /test_data (see scripts/make_fixtures.py)
│   └── ...
└── pyproject.toml
```

## Setup (once code exists)

This project uses [uv](https://docs.astral.sh/uv/) (or plain `pip`) against `pyproject.toml`.

```
uv sync
uv run pytest
uv run uvicorn app.main:app --reload
```

## Non-negotiable constraints

See `/CLAUDE.md` — in particular: `Decimal` end-to-end for all points/percentage/threshold
arithmetic (never `float`, never SQLite `REAL`), and the §5.3 import row-count checksum must
hard-fail rather than silently import a partial student list.
