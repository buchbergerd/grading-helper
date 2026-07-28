# CLAUDE.md

Guidance for an agent working in this repo.

## Source of truth

**`SPECIFICATION.md` (repo root) is the spec. Read the relevant section before touching related
code — do not rely on a summary of it, including this one.** This file does not restate the
spec; it only points at it and lists the constraints an implementation is most likely to violate
silently. If this file and the spec ever disagree, the spec wins — fix this file.

Current status: **milestones 1–3 backend (§15.1-§15.3) done** — data model, auth/accounts,
Lecture/Exam CRUD, registration-PDF import, attendance-list PDF, the grading engine
(`app/grading/engine.py`), and the points/attendance entry API (`app/api/points.py`) over all of
it, plus the React UI for milestones 1–2. Still open for §15.3: the points-entry frontend. Next
up per §15 is §15.4 (internal report: shared statistics module, PDF + interactive dashboard).
`SPECIFICATION.md` §15 gives the intended build order — follow it rather than jumping to
whichever feature seems easiest.

`docs/api-contract.md` is the backend↔frontend HTTP contract. Keep it in sync when you change
an endpoint; it exists so neither side has to read the other's code.

## Repo layout

- `backend/` — FastAPI app. See `backend/README.md` for intended package layout.
- `frontend/` — React/Vite/TypeScript app. See `frontend/README.md`.
- `deploy/` — Docker/compose skeletons. See `deploy/README.md`.
- `docs/open-questions.md` — tracked register of the spec's open assumptions (§14). Check it
  before making a call on something the spec left open; update it when you resolve or add one.
- `backend/scripts/` — dev tooling: `create_admin.py` (bootstrap the first account) and
  `make_fixtures.py` (regenerates the synthetic registration PDFs in `test_data/`; the generated
  PDFs are committed, so tests never need Typst to run).
- `test_data/` — anonymized/synthetic registration-PDF fixtures only. Never add a real
  (non-anonymized) export here — see the root `.gitignore` and `test_data/README.md`.

## Commands (once code exists)

```
cd backend && uv sync && uv run pytest && uv run uvicorn app.main:app --reload
cd frontend && npm install && npm run dev
```

## Invariants — violate these silently and the bug won't show up until grading is wrong

These are called out because the spec explicitly flags them as the likely failure modes for an
implementing agent, not because they're exhaustive. Section references are to `SPECIFICATION.md`.

- **Decimal arithmetic end-to-end, never binary float** (§7.0). All points/percentages/
  thresholds use `decimal.Decimal`, from parsing input through storage through the grade
  comparison. SQLite's `REAL` affinity is a binary float — points/percentage columns must be
  `TEXT`/`NUMERIC` with explicit `Decimal` conversion on read/write, never a bare `REAL` column.
- **Decimals cross the HTTP boundary as JSON strings, never JSON numbers.** A JSON number is an
  IEEE-754 double in every JS client and in most Python decoders, so allowing one would undo
  §7.0 at the API layer no matter how clean the storage is. The backend rejects a JSON number in
  a decimal field with `422` — including a JSON *integer* — and the frontend keeps these values
  as `string` from the wire to the input field and back (`type="text"`, never `type="number"`).
- **Point values are stored as TEXT and therefore do not sort or compare numerically in SQL**
  (`"10.0" < "9.0"`). Every ordering, filtering, aggregation or min/max over points or
  percentages must happen **in Python** on decoded `Decimal`s — never in `ORDER BY`, `WHERE` or
  `SUM()`. This matters most for §9's statistics and the §10/§11 report queries.
- **Threshold rounding rule** (§7.2): `threshold_points(grade) = floor((pct/100 * max_points) /
  0.5) * 0.5`. A student's grade is the best (lowest) grade whose threshold is met by
  `final_total`. Use the §7.5 table as literal test cases, not just a guideline.
- **Bonus mode semantics** (§7.3): `ONLY_IF_PASSING_WITHOUT_BONUS` checks `raw_total` alone
  against the passing (4.0) threshold *before* deciding whether bonus applies at all — it is not
  "cap the final grade at pass." Get this backwards and bonus silently turns fails into passes.
- **Attendance overrides grade** (§7.4): `attended = false` → "n.e.", full stop, regardless of
  points. `attended = true` and below passing → "nicht bestanden" (text, not a number).
- **Import completeness check must hard-fail, not warn** (§5.3): parsed `Nr.` values must be a
  contiguous `1..N`, **and** every page the `Seite X von Y` footer declares must be present. A
  silently-dropped page is named in the spec as the single worst realistic failure mode for this
  app — do not relax either check to a warning for convenience. The two are not redundant:
  dropping the **last** page leaves the surviving rows a clean contiguous `1..N`, so only the
  page check catches it (`test_dropping_the_last_page_is_caught_by_the_footer_check_alone`).
- **`module_title` is captured verbatim per course PDF and never normalized or cross-checked**
  against other PDFs in the same Exam (§4, §5.1) — a Kombinationsprüfung legitimately has a
  different module name/CP/BPO version per course. `course_code` (the short parenthetical) is
  the separate, deliberately-normalized grouping/sort key — don't conflate the two.
- **DIN 5007-1 German collation for the attendance-list sort** (§6), not codepoint order
  ("Öztürk" sorts under O; ß ≍ ss). Sort names exactly as printed in the source PDF — no
  nobiliary-particle reordering. Use `app/collation.py::german_sort_key`; never `str.sort()`,
  and never an SQL `ORDER BY` on a name column — that *is* the codepoint sort §6 warns about.
  Sorting happens in Python, in the report layer.
- **Completeness gate blocks exports** (§8.1): the examination-office and student-results
  reports must refuse to generate if any non-excluded student is missing attendance or (when
  attended) any exercise point. Never substitute an implicit zero.
- **Editing max_points or the grading schema after points exist triggers a full, visible
  recomputation** (§8.1) — grade thresholds must never shift silently under data an instructor
  may have already transcribed onto paper exams.
- **Excluded ≠ deleted** (§5.3): `excluded` is a boolean flag kept in the database for audit,
  never a row deletion. Excluded students are omitted from every list/report/grade but stay
  queryable.
- **Admins do not see exam data by default** (§3) — account management only. This is a
  least-privilege default, not yet confirmed with the user; don't casually add a "view as admin"
  feature.
- **Everything user-facing is German**: UI text, PDF/Excel report content, number formatting
  (comma decimal separator, "1,3") and dates (DD.MM.YYYY). This repo's own docs/comments are in
  English — don't let that leak into generated output.
- **No outbound network calls at runtime** (§13): Typst's `cetz`/`cetz-plot` packages must be
  vendored into the image at build time, not fetched from the `@preview` registry when a report
  is generated. Same principle for anything else — this app must work fully offline once deployed.
- **Exam data is real personal data** (names, Matrikelnummern) — don't log it, don't put it in
  test fixtures that aren't clearly synthetic/anonymized (see `test_data/README.md`), don't
  include it in error messages that might end up in shared logs.

## Skills

None yet, deliberately. This file is always in an agent's context; a skill only fires when
invoked, which is the wrong mechanism for constraints like the ones above that must always
apply. Worth adding later once there's real code and a *repeatable procedure* over it — e.g. a
"check new grading-logic changes against §7.5's worked example" review pass, or a fixture
regeneration runbook for `scripts/`. Don't add a skill just to have one.
