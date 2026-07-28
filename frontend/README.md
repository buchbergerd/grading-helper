# GradingHelper frontend

React + Vite + TypeScript. See `/SPECIFICATION.md` for the full spec and `/CLAUDE.md` for the
invariants an implementation must not violate.

Status: milestone 1 (§15.1) — login, Lecture/Exam CRUD, admin account management. Points entry
(§8), reports (§9–§11) and the interactive dashboard (§9) are later milestones.

## Layout

```
frontend/
├── src/
│   ├── api/client.ts      # typed client for the FastAPI backend (docs/api-contract.md)
│   ├── auth/              # AuthContext, RequireAuth / RequireAdmin route guards
│   ├── pages/             # Login, Lecture list/detail, Exam detail, Admin users, ...
│   ├── components/        # shared message rendering, confirm dialog
│   ├── grading/preview.ts # the ONLY arithmetic module — see the decimal rule below
│   ├── util/format.ts     # German number/date formatting (§14 #6)
│   └── index.css          # one plain stylesheet
├── vite.config.ts         # dev proxy /api -> :8000, vitest config
└── package.json
```

There is no `src/i18n/`: all UI text is German and written inline. A translation layer would be
dead weight — the app is German-only by specification, not German-by-default.

## The decimal rule (§7.0) — the one thing not to get wrong here

**Point and percentage values are `string` from the API to the input field and back. They never
become a JavaScript `number`.** A JS number is an IEEE-754 double, which is exactly what §7.0
forbids: it would turn `"12.50"` into `12.5` (silently dropping a trailing zero the instructor
typed) and let arithmetic land on the wrong side of a grade boundary.

Consequences, all enforced by tests:

- Points/percentage inputs are `type="text" inputMode="decimal"`, **never** `type="number"` —
  that input exposes `valueAsNumber` and normalises what the user typed.
- `src/util/format.ts` does comma↔dot conversion by string surgery only.
- All arithmetic lives in `src/grading/preview.ts` as `bigint` hundredths, and everything it
  produces is labelled a preview: **the backend's computation is the authoritative one.**
- `Number()` appears exactly once, in `src/util/id.ts`, for route ids (integers, not decimals).
  `grep -rnE "Number\(|parseFloat|parseInt|valueAsNumber|toFixed" src/` should return only that
  file and comments.

## Deferred decisions

- **Charting library** for the interactive internal-report dashboard (§9): spec explicitly leaves
  this open (e.g. Chart.js / Recharts / Plotly) — decide and pin when building that page, not
  before. Not in `package.json` yet.

## Commands

```
npm install
npm run dev      # needs the backend on :8000 (cd ../backend && uv run uvicorn app.main:app --reload)
npm run test     # vitest
npm run build    # tsc -b && vite build
```
