# API contract — milestone 1 (§15.1: data model, auth, Lecture/Exam CRUD)

Written before implementation so the backend and frontend agree without one having to read the
other's code. Section references are to `/SPECIFICATION.md`. Later milestones extend this file.

All routes are under `/api`. All request/response bodies are JSON. **Decimal-valued fields
(`max_points`, `percentage`, `bonus_points`, points) cross the wire as JSON *strings***, never
JSON numbers — a JSON number is parsed as an IEEE-754 double by every JS client and by most
Python JSON decoders, which would defeat §7.0 at the API boundary. The frontend keeps them as
strings and never does arithmetic on them; the backend parses them with `Decimal(str)`.

The backend **enforces** this rather than being lenient about it: a JSON number in a decimal
field is a `422`, and that includes a JSON *integer* (`{"percentage": 95}` is rejected; send
`{"percentage": "95"}`). Also rejected: exponent notation (`"1e2"`), `"NaN"`/`"Infinity"`, and
the empty string. Responses preserve the stored representation exactly, trailing zeros included
— `"12.50"` in comes back as `"12.50"`, and a sum can gain a decimal place
(`"12.50" + "12.5"` → `total_max_points: "25.00"`), so compare these as decimals, not strings.

## Auth (§3)

Session is a DB-backed opaque token in an **HttpOnly, SameSite=Lax** cookie (`gh_session`),
12 h absolute expiry. Not JWT: §3 requires account deactivation and password reset to take
effect immediately, which needs server-side revocation. All non-auth routes require a valid
session; the frontend never reads the cookie.

| Method | Path | Body | Response |
|---|---|---|---|
| POST | `/api/auth/login` | `{username, password}` | `200` + sets cookie, `{id, username, is_admin}`; `401` on bad credentials **or** deactivated account (same message either way — don't reveal which) |
| POST | `/api/auth/logout` | — | `204`, deletes the session row and clears the cookie. **Idempotent**: also `204` with a missing, expired or already-revoked cookie — a `401` here would strand a dead cookie in the browser with no way to clear it |
| GET | `/api/auth/me` | — | `{id, username, is_admin}`; `401` if no/expired session |
| POST | `/api/auth/password` | `{current_password, new_password}` | `204` — self-service change; verifies `current_password` |

## Account management (admin only, §3)

Admins manage accounts and **must not** be able to read exam data (§14 #5, least privilege).
Non-admins get `403` here (existence of the admin API is not secret; individual exam data is).

| Method | Path | Body | Response |
|---|---|---|---|
| GET | `/api/admin/users` | — | `[{id, username, is_admin, is_active, created_at}]` |
| POST | `/api/admin/users` | `{username, password, is_admin?}` | `201` + user; `409` if username taken |
| PATCH | `/api/admin/users/{id}` | `{is_active?, is_admin?}` | `200` + user. Deactivating **also deletes that user's sessions** (immediate revocation). An admin cannot deactivate or demote their own account (prevents locking the last admin out) → `400` |
| POST | `/api/admin/users/{id}/password` | `{new_password}` | `204` — reset; also deletes that user's sessions |

## Lectures (§4)

Scoped to `Lecture.owner`. A lecture owned by someone else responds **`404`, not `403`** —
`403` confirms the row exists, which is an existence leak over other instructors' data.

| Method | Path | Body | Response |
|---|---|---|---|
| GET | `/api/lectures` | — | `[{id, name, created_at, exam_count}]`, owner's only |
| POST | `/api/lectures` | `{name}` | `201` + lecture |
| GET | `/api/lectures/{id}` | — | lecture + its exams (summary shape below) |
| PATCH | `/api/lectures/{id}` | `{name}` | `200` + lecture |
| DELETE | `/api/lectures/{id}` | — | `204`, cascades to exams and all their data (§13). `409` unless `?confirm=true` is passed, since this destroys grades |

## Exams (§4)

Scoped to `Exam.owner`, **not** the parent lecture's owner: §4 makes the exam owner default to
the lecture's owner but stay independently editable, so authorizing via the lecture would let a
reassignment silently break access control. Same `404`-not-`403` rule.

Exam summary: `{id, lecture_id, lecture_name, semester, termin, exam_date, bonus_mode, owner_id}`.
Exam detail additionally: `{exercises: [...], grading_schema: [...], registration_count,
total_max_points, recomputation_warning}`.

- `exam_date`: `YYYY-MM-DD` or `null` on the wire (German `DD.MM.YYYY` formatting is a
  presentation concern — §14 #6 — applied in the UI and in reports, not in the API).
- `bonus_mode`: `"ALWAYS" | "ONLY_IF_PASSING_WITHOUT_BONUS"` (§7.3).
- `exercises`: `[{id, name, max_points: "12.5", position}]` — ordered by `position`. On a write,
  `id` and `position` are accepted (so a detail response can be sent straight back) but
  **ignored**: positions are renumbered `1..N` server-side in submitted order, and identity is
  not preserved across a replace. `max_points` must be `> 0`.
- `grading_schema`: `[{grade: "1.0", percentage: "95", threshold_points: "57.0"}]` — all ten
  grades of §7.1, strictly decreasing percentages (§7.2). Grades are strings, never JSON
  numbers. A schema is either **absent/empty or complete**; a partial one is a `422`.
  `threshold_points` is response-only, computed server-side from `total_max_points` per §7.2
  (the backend is authoritative; the frontend may preview but never sends it).
- `total_max_points`: sum of the exercises' `max_points`, as a string. Response-only.
- `recomputation_warning`: `{"changed": bool, "affected_registrations": int}` or `null`
  (§8.1). Non-`null` only on a `PATCH` response, and only when `exercises` or `grading_schema`
  were replaced **and** the exam already has registrations — i.e. grade thresholds just moved
  under existing student data and the UI must say so visibly. `affected_registrations` counts
  registrations that already carry attendance or points; in M1 nothing writes either, so it is
  always `0` until §15.3 lands.
- `lecture_name` is a convenience copy of the parent lecture's name; it is never derived from a
  registration PDF (§4).

| Method | Path | Body | Response |
|---|---|---|---|
| GET | `/api/exams?lecture_id=` | — | `[exam summary]`, the caller's own, most recent sitting first. `lecture_id` is a plain filter, **not** an ownership check on the lecture: a reassigned exam may hang under someone else's lecture and must stay listable by its owner. An unknown/foreign `lecture_id` yields `[]` |
| POST | `/api/lectures/{id}/exams` | `{semester, termin, exam_date?, bonus_mode?, exercises?, grading_schema?}` | `201` + exam detail. Omitted `bonus_mode`/`exercises`/`grading_schema` are copied forward from the lecture's most recent prior exam (§4 lists all three) — a one-time copy, nothing stays linked. If there is no prior exam they start empty and `bonus_mode` defaults to `"ALWAYS"`. **Sending the field explicitly always wins, including an empty list** — "no exercises yet" must be expressible, so absent ≠ `[]` |
| GET | `/api/exams/{id}` | — | exam detail |
| PATCH | `/api/exams/{id}` | any of the create fields, plus `owner_id` | `200` + exam detail. Replacing `exercises`/`grading_schema` is a full replace, not a merge |
| DELETE | `/api/exams/{id}` | — | `204`, cascades to registrations and points (§13). `409` unless `?confirm=true` |

`owner_id` may only be set to an existing **active** user, else `422` with the German-message
shape below. Reassigning transfers access — the previous owner then gets `404` on that exam.

"Most recent prior exam" (§4 leaves this open; the backend pins it): `exam_date` **descending**,
a `null` `exam_date` counting as **oldest**, ties broken by `id` descending. The same ordering is
used for the exam lists in `GET /api/exams` and in a lecture's detail response.

### Validation errors

Grading-schema and exercise validation failures return `422` with
`{"detail": {"errors": ["<German message>", ...]}}` — the messages come from
`app/grading/schema.py::validate_grading_schema` (§7.2) and are shown to the instructor verbatim.
Standard FastAPI/Pydantic validation errors keep their default `422` shape.

**Password-policy failures use the same `{"detail": {"errors": [...]}}` shape** (on admin create,
admin reset, and self-service change), so the frontend needs only one German-message renderer.

## Deferred to later milestones

Registration import (§5), attendance list (§6), points entry (§8), reports (§9–§11). Editing
`max_points` or the grading schema after points exist must trigger a visible recomputation
(§8.1) — there are no points yet in M1, but the PATCH handler is where that hooks in: it already
returns `recomputation_warning` and calls `app/api/exams.py::count_affected_registrations`, which
M3 must extend from counting to actually recomputing `final_total`/`grade`.
