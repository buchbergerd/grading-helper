# API contract (living document — extended each milestone)

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
24 h lifetime as a **sliding window**: every request through `current_session` (i.e. every
authenticated request, not just login) pushes the server-side `expires_at` and the cookie's
`Max-Age` out to a fresh 24 h (§14 #12) — an instructor working continuously is never logged out
mid-session; 24 h of inactivity still expires it. Not JWT: §3 requires account deactivation and
password reset to take effect immediately, which needs server-side revocation. All non-auth
routes require a valid session; the frontend never reads the cookie.

| Method | Path | Body | Response |
|---|---|---|---|
| POST | `/api/auth/login` | `{username, password}` | `200` + sets cookie, `{id, username, is_admin}`; `401` on bad credentials **or** deactivated account (same message either way — don't reveal which) |
| POST | `/api/auth/logout` | — | `204`, deletes the session row and clears the cookie. **Idempotent**: also `204` with a missing, expired or already-revoked cookie — a `401` here would strand a dead cookie in the browser with no way to clear it |
| GET | `/api/auth/me` | — | `{id, username, is_admin}`; `401` if no/expired session |
| POST | `/api/auth/password` | `{current_password, new_password}` | `204` — self-service change; verifies `current_password`; `422` + `{"detail": {"errors": [German strings]}}` if the new password fails policy (§14 #13/#14 — same shape as grading-schema validation errors, the app's one shape for "show these German messages verbatim") |

## Account management (admin only, §3)

Admins manage accounts and **must not** be able to read exam data (§14 #5, least privilege).
Non-admins get `403` here (existence of the admin API is not secret; individual exam data is).

| Method | Path | Body | Response |
|---|---|---|---|
| GET | `/api/admin/users` | — | `[{id, username, is_admin, is_active, created_at}]` |
| POST | `/api/admin/users` | `{username, password, is_admin?}` | `201` + user; `409` if username taken; `422` + `{"detail": {"errors": [...]}}` on a policy-failing password (§14 #13/#14) |
| PATCH | `/api/admin/users/{id}` | `{is_active?, is_admin?}` | `200` + user. Deactivating **also deletes that user's sessions** (immediate revocation). An admin cannot deactivate or demote their own account (prevents locking the last admin out) → `400` |
| POST | `/api/admin/users/{id}/password` | `{new_password}` | `204` — reset; also deletes that user's sessions; same `422` shape as above on policy failure |

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
  `position` is accepted (so a detail response can be sent straight back) but **ignored**:
  positions are renumbered `1..N` server-side in submitted order. `id` **is** meaningful: an item
  whose `id` matches one of the exam's current exercises updates that row in place rather than
  replacing it, which is what keeps that exercise's already-entered `ExercisePoints` alive across
  the edit — e.g. adding one more exercise, reordering, or renaming must not wipe the others'
  points. An absent or unmatched `id` creates a new exercise; an existing exercise with no
  matching item in the submission is removed (and its points with it — the one case where that's
  correct). `max_points` must be `> 0`.
- `grading_schema`: `[{grade: "1.0", percentage: "95", threshold_points: "57.0"}]` — all ten
  grades of §7.1, strictly decreasing percentages (§7.2). Grades are strings, never JSON
  numbers. A schema is either **absent/empty or complete**; a partial one is a `422`.
  `threshold_points` is response-only, computed server-side from `total_max_points` per §7.2
  (the backend is authoritative; the frontend may preview but never sends it).
- `total_max_points`: sum of the exercises' `max_points`, as a string. Response-only.
- `recomputation_warning`: `{"changed": bool, "affected_registrations": int, "grades_changed":
  int}` or `null` (§8.1). Non-`null` only on a `PATCH` response, and only when `exercises` or
  `grading_schema` were replaced **and** the exam already has registrations — i.e. grade
  thresholds just moved under existing student data and the UI must say so visibly.
  `affected_registrations` counts registrations that already carry attendance or points (a
  coarse "could this edit matter" count). `grades_changed` is the precise one: the number of
  non-excluded registrations whose **computed grade string** differs before vs. after the edit —
  taken as a snapshot immediately before the mutation and re-derived immediately after. A
  registration whose grade was not previously computable (schema never configured, or attendance
  not yet recorded) and now is counts as changed. Deliberately stricter than
  `affected_registrations`: a percentage edit that still floors to the same 0.5-point threshold
  (§7.2) reports `grades_changed: 0` even though registrations carry data — an always-firing
  warning teaches instructors to ignore it. Nothing here is a stored column (no `Exam`/
  `StudentRegistration` field holds a grade); "recomputing" means re-deriving it from
  `ExercisePoints` and the exam's current exercises/schema, same as every other read.
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

## Registrations (§5.3) — milestone 2

All routes owner-scoped through the exam; another instructor gets `404`, not `403`.

`RegistrationOut`: `{id, exam_id, matrikelnummer, nachname, vorname, course_code, module_title,
versuch, kommentar, flagged, excluded, attended, bonus_points, source_filename}`.
`matrikelnummer` is a **string** (leading zeros are meaningful); `bonus_points` is a decimal and
so follows the string rule above; `attended` is `true | false | null`, where `null` means "not
yet recorded" and is deliberately distinct from `false` ("nicht erschienen", §7.4).

| Method | Path | Body | Response |
|---|---|---|---|
| POST | `/api/exams/{id}/registrations/import` | `multipart/form-data`: repeated field **`files`** (one or more PDFs) + optional `replace_existing` | `201` + `{imported_total, replaced_count, files: [...], warnings: [...]}` |
| GET | `/api/exams/{id}/registrations` | — | `[RegistrationOut]`, optional `course_code` filter |
| POST | `/api/exams/{id}/registrations` | `{matrikelnummer, nachname, vorname, course_code, module_title, versuch, kommentar?, flagged?, excluded?}` | `201` — the manual late-registration path (§5.3). `course_code`/`module_title` are required: there is no PDF to take them from |
| PATCH | `/api/registrations/{id}` | any subset, incl. `excluded`, `attended`, `bonus_points` | `200` + `RegistrationOut` |
| DELETE | `/api/registrations/{id}` | — | `204`. A **real** deletion, for a row added in error — distinct from `excluded` |
| DELETE | `/api/exams/{id}/registrations?confirm=true` | — | `204`. Deletes **every** registration of the exam (including excluded ones) and, by cascade, all their `ExercisePoints` — "Alle entfernen", a reset of the import. `409` unless `?confirm=true`. Distinct from `excluded`: that flag hides a student while keeping their data for audit (§5.3); this route destroys the rows and any grade already entered for them, with no undo |
| GET | `/api/exams/{id}/registrations/count` | — | `{total, per_course: [{course_code, count}]}`, excluded students already omitted (§6's head count) |

Import semantics, all from §5.3:

- **One PDF = one Studiengang** (§5.1). Each row is tagged with **its own file's** `course_code`
  and `module_title`. `module_title` is stored verbatim and is never normalised or cross-checked
  between files — a Kombinationsprüfung legitimately differs per course (§4).
- **The whole request is atomic**, across all files, not per file. §5.3 forbids a partial import,
  and a half-imported exam is exactly the state where a student silently never gets a grade.
- **`semester`/`termin` mismatch** between files (or against the exam) is a **warning**, not a
  block — it usually means the wrong file was picked, but it is not corrupting.
- **Duplicate Matrikelnummer** within the request or against already-imported rows is a `422`
  requiring manual resolution — never a silent merge. The payload names the Matrikelnummer and
  describes each competing occurrence so the UI can offer a choice.
- **Kommentar ≠ `(angemeldet)`** → imported with `flagged: true`, never dropped and never
  silently kept unmarked. The instructor decides per row whether to exclude it.
- `replace_existing=true` first deletes this exam's existing registrations for the same
  `course_code` — the "I uploaded the wrong file" path. Off by default.

Failures return `422` with `{"detail": {"errors": [German messages], ...}}`, optionally carrying
`files` (per-file parse errors) and `duplicates` (structured duplicate occurrences). The German
messages are shown verbatim; per §5.3 these are hard failures the instructor must act on.

## Reports — milestone 2

| Method | Path | Response |
|---|---|---|
| GET | `/api/exams/{id}/reports/attendance-list` | `200 application/pdf` + `Content-Disposition` (ASCII fallback plus RFC 5987 `filename*` for umlauts) |

Columns are Studiengang, Matr.-Nr., Nachname, Vorname plus a tick column, sorted by course then
surname with **German DIN 5007-1 collation** (§6) computed in Python — never in SQL. Excluded
students are omitted. An exam with no registrations still renders a valid PDF with a head count
of 0 rather than erroring.

## Points / attendance entry (§8, §8.1) — milestone 3

All routes owner-scoped through the exam (`/api/exams/{id}/...`) or through the registration's
exam (`/api/registrations/{id}/points`); another instructor gets `404`, not `403`, exactly as
elsewhere. `app/api/points.py` owns all four routes.

Neither `final_total` nor `grade` is ever a stored column — every response computes them fresh
from `ExercisePoints`, `bonus_points`, `attended` and the exam's current exercises/grading schema
(`app/grading/engine.py::compute_grade`). There is nothing to keep in sync; there is only ever
"read it now".

`PointsEntryOut` (one registration's row, used by the grid and by both save routes):
`{id, matrikelnummer, nachname, vorname, course_code, versuch, attended, bonus_points, points:
{<exercise_id as string>: "value"}, raw_total, final_total, grade, status, is_complete}`.
`points` only carries exercises that actually have an `ExercisePoints` row — a missing key means
"not entered", never an implicit zero (§8.1). `status` is the grading engine's English
`GradeStatus` token (`GRADED` / `FAILED` / `NOT_ATTENDED` / `ATTENDANCE_NOT_RECORDED`) — for UI
branching only, never shown to a user; display `grade` instead. `grade` and `status` are `null`
whenever the parent response's `grading_configured` is `false` (schema absent or incomplete),
regardless of what is otherwise entered. `raw_total` is **always** present (the sum of whatever
is entered is a plain fact, independent of attendance or the schema); `final_total` is `null`
exactly when `status` is `NOT_ATTENDED` or `ATTENDANCE_NOT_RECORDED` — the two are not jointly
nullable, and a client must not hide `raw_total` just because `final_total`/`grade` are absent.

| Method | Path | Body | Response |
|---|---|---|---|
| GET | `/api/exams/{id}/points` | — | `{exercises: [...], grading_schema: [...], bonus_mode, grading_configured, entries: [PointsEntryOut]}`. Optional `course_code` filter. One entry per **non-excluded** registration (§5.3), sorted by Matrikelnummer |
| PUT | `/api/registrations/{id}/points` | `{attended?, bonus_points?, points?}` | `200` + `{registration: PointsEntryOut, warnings: [German strings]}` |
| PUT | `/api/exams/{id}/points` | `{entries: [{registration_id, attended?, bonus_points?, points?}, ...]}` | `200` + `{entries: [PointsEntryOut], warnings: [...]}`. One transaction, all rows or none |
| GET | `/api/exams/{id}/completeness` | — | `{is_complete, incomplete_count, incomplete_students: [{id, matrikelnummer, nachname, vorname, attendance_missing, missing_exercises: [names]}]}` (§8.1) |

**Both `PUT` routes are a full replace of each row's entry state, never a merge** — this is the
one place in the API where "field absent" does *not* mean "leave unchanged":

- a `points` map key that is absent from the payload, or present with JSON `null`, **deletes**
  that exercise's `ExercisePoints` row (never coerced to a stored zero — §8.1 requires "not
  entered" and "entered zero" to stay distinguishable);
- an absent `attended` sets it to `null` ("not yet recorded");
- an absent `bonus_points` sets it to `"0"`.

Points entered above an exercise's `max_points` are **saved anyway** and reported back in
`warnings` — never rejected, never silently clamped (§8: "typos happen"). Negative points or
negative `bonus_points`, and any write to an **excluded** registration, are rejected with `422`.
Marking `attended = false` does **not** clear previously entered `points` — resending the same
`points` map while flipping `attended` keeps that data in the database (flipping back to `true`
later does not require re-transcribing the exam), while §7.4 means those points play no role in
the grade (`"n.e."`) for as long as `attended` stays `false`.

The bulk `PUT /api/exams/{id}/points` validates every row — including that its
`registration_id` actually belongs to this exam, is not excluded, and appears at most once in
the request — before writing anything; a single invalid row rejects the whole batch with `422`
and leaves the database exactly as it was.

`GET /api/exams/{id}/completeness` is §8.1's gate for the (not-yet-built) §10/§11 report routes:
every non-excluded registration must have `attended` recorded, and every `attended = true`
registration must have every exercise's points entered. `app/api/points.py::exam_completeness`
is the shared helper the future report routes call directly rather than re-deriving the same
list. Excluded students are never counted.

## Internal report / statistics (§9) — milestone 4

Owner-scoped like every other exam route: a non-owner — **including an admin** — gets `404`, not
`403` (§3's least-privilege default, restated by §9: "visible only to the exam's owner (and, per
§3, not to admins by default)").

| Method | Path | Response |
|---|---|---|
| GET | `/api/exams/{id}/statistics` | `200` + `ExamStatistics` (below) |
| GET | `/api/exams/{id}/reports/internal` | `200 application/pdf` + `Content-Disposition`, same header convention as the attendance list |

**Neither route is gated by the §8.1 completeness check.** That gate is for the §10/§11 exports
only. §9 is explicitly a live view over grading in progress, so a half-graded exam gets a `200`
that reports how much is still missing — never a `409`.

Both routes serve the output of one function, `app/statistics.py::build_exam_statistics`, which
§9 requires to be the single source of these numbers so the PDF and the dashboard can never
disagree. The JSON route returns that payload verbatim (no pydantic response model: the payload
is already the wire shape, and pydantic's lax coercion could turn a stray `float` into a
plausible-looking string instead of failing).

### `ExamStatistics`

Top level: `exam_id`, `lecture_name`, `semester`, `termin`, `exam_date` (`DD.MM.YYYY` or `null`),
`generated_at` (`DD.MM.YYYY HH:MM`), `max_points`, `bonus_mode`, `grading_configured`,
`passing_threshold`, `counts`, `rates`, `grade_distribution`, `total_points_histogram`,
`exercise_histograms`, `versuch_breakdown`.

Three rules the frontend depends on — all three exist so no renderer ever computes anything:

- **Decimals are canonical strings**, as everywhere else in this API. `percent` values carry one
  decimal place, mean/median grades two, both already rounded `ROUND_HALF_UP` **in the backend**.
  A renderer that rounds again can make the PDF and the dashboard disagree.
- **Every rate is `{numerator, denominator, percent}`** — `percent` is `null` when `denominator`
  is 0 (render as "—"). Never divide the counts client-side. `rates.attendance` is
  attended/registered; `rates.passing` and `rates.failure` divide by `counts.graded` (attended
  **and** complete), *not* by `attended`, or an in-progress exam understates its failure rate.
- **Histogram bin captions arrive pre-formatted in German, in explicit interval notation**
  (`label`, e.g. `[12;13[` for an ordinary bin, `[63;64]` for a histogram's closed last bin —
  see §9's half-open/closed rule); `lower` and `upper` are there for tooltips and axis work, not
  for rebuilding the caption.

`counts` distinguishes `not_attended` (`attended = false`) from `attendance_not_recorded`
(`attended = null`), and carries `incomplete`: students who attended but are missing at least one
exercise entry. Five buckets always partition `registered`:
`graded + incomplete + awaiting_schema + not_attended + attendance_not_recorded`.
`awaiting_schema` holds students who attended with every point entered but whose exam has no
grading schema yet — entering points before configuring the schema is an ordinary order to work
in, and they must not vanish from the counts. Those are **omitted from `grade_distribution` and `total_points_histogram`** — a
partial sum would render as a fake "nicht bestanden" — while their entered exercise points still
count in that exercise's own histogram. Both views must surface `incomplete` prominently so a
half-graded distribution is never read as final. `registered` counts non-excluded registrations
only (§5.3); `excluded` is reported alongside it purely to explain the difference.

Histogram bins are half-open `[lower, upper)` except the last, which is closed. **The bin range
is derived from the observed maximum, not from `reference_max`**: an uncapped `ALWAYS` bonus
(§7.3) and an over-max exercise entry (§8 warns but does not clamp) both exceed it, and students
above `reference_max` must not fall off the chart. `versuch_breakdown` lists only attempt numbers
that actually occur, ascending — not assumed dense or capped.

Both `total_points_histogram` and every entry in `exercise_histograms` default to a **1.0-point**
bin width (`bin_width` in the payload, a canonical decimal string). §9's spec text suggests `0.5`
for exercise histograms; that default was changed to `1.0` at the user's explicit request so it
matches the total-points histogram — §9 itself calls its bin widths "a sensible default, not a
hard requirement". `build_exam_statistics`'s `total_points_bin_width`/`exercise_bin_width`
keyword arguments (`app/statistics.py`) can still override either independently per call.

The full field-by-field contract with the reasoning behind each decision lives in
`backend/app/statistics.py`'s TypedDicts; `frontend/src/api/client.ts` mirrors them.

## Examination office report (§10) — milestone 5

Owner-scoped like every other exam route (`404`, not `403`, for another instructor's exam).
Both routes run the same export gate before touching `app/reports/examination_office.py`:
`app/api/points.py::exam_completeness` (§8.1 — every non-excluded registration needs `attended`
recorded, and every attended one needs every exercise's points entered) **and** the grading
schema being fully configured (all ten §7.1 grades present). Either failing returns

```
409 {"detail": {"errors": ["<German message>", ...]}}
```

— a list of German strings under `detail.errors`, the same shape §5.3's import failures and
§4/§8's `422` validation errors use, but at `409` because this is "blocked by current server
state" (an otherwise well-formed exam that isn't export-ready yet), not a malformed request.
Up to two messages can appear together: one naming the incomplete-registration count
("`N Anmeldung(en) sind noch unvollständig (Anwesenheit oder Punkte fehlen).`") and/or one
saying the grading schema isn't configured yet — a client should render the whole list, not
assume there is exactly one error.

| Method | Path | Response |
|---|---|---|
| GET | `/api/exams/{id}/reports/examination-office/pdf` | `200 application/pdf` + `Content-Disposition`, same header convention as the attendance list, or `409` per above |
| GET | `/api/exams/{id}/reports/examination-office/excel` | `200 application/vnd.openxmlformats-officedocument.spreadsheetml.sheet` + `Content-Disposition`, or `409` per above |

Structure: one section per **`(course_code, module_title)` pair**, not per `course_code` alone —
a Kombinationsprüfung can legitimately show two sections sharing a `course_code` with a
different verbatim `module_title` (§4/§5.1). Sections are German-collated
(`app/collation.py::german_sort_key`) on that pair; rows within a section are sorted by
Matrikelnummer as a plain string. Excluded registrations are omitted (§5.3). The PDF's table
columns are Matr.-Nr., Nachname, Vorname, Note; the Excel export is one flat sheet (no
per-section tabs) with an added `Modultitel` column to compensate for the lost visual grouping,
and every cell — including Matr.-Nr. — is written as a string so a leading zero survives and
`Note`'s mixed numeric/text values ("1,3" / "nicht bestanden" / "n.e.") round-trip unchanged.
An exam with zero non-excluded registrations trivially satisfies the completeness gate (nothing
is incomplete) and, once its grading schema is configured, both routes render a valid document
with zero sections and an explanatory line rather than erroring — the gate blocks an
*incomplete* exam, not an *empty* one.

## Student results report (§11) — milestone 5

Owner-scoped like every other exam route (`404`, not `403`, for another instructor's exam).
Both routes reuse the exact same `_require_exportable` gate as the examination-office report
above (`app/api/reports.py`) — §8.1 completeness plus a fully configured grading schema — and the
same `409 {"detail": {"errors": [...]}}` shape on failure.

| Method | Path | Response |
|---|---|---|
| GET | `/api/exams/{id}/reports/student-results/pdf` | `200 application/pdf` + `Content-Disposition`, same header convention as the attendance list, or `409` per above |
| GET | `/api/exams/{id}/reports/student-results/excel` | `200 application/vnd.openxmlformats-officedocument.spreadsheetml.sheet` + `Content-Disposition`, or `409` per above |

Much simpler than §10: **no course/module grouping and no names at all** — one flat list sorted
by Matrikelnummer as a plain string, matching common practice of posting anonymized grade lists.
The PDF's and Excel's only two columns are Matr.-Nr., Note, in that order; the Excel export
writes every cell — including Matr.-Nr. — as a string, same reasoning as §10 (a leading zero
survives, and `Note`'s mixed numeric/text values round-trip unchanged). An exam with zero
non-excluded registrations renders a valid document with zero rows and an explanatory line,
same posture as §10's zero-sections case.
