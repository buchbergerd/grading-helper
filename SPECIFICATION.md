# GradingHelper — Specification

Internal tool for managing written-exam grading workflows in a university department: importing
registered students, tracking attendance and points, computing grades from a configurable
grading schema, and generating the PDF/Excel reports needed internally, for the examination
office, and for students.

All UI text, PDF/Excel report content, and generated documents are in **German**. This
specification is in English for the implementing developer/agent.

---

## 1. Glossary (German terms used throughout)

| Term | Meaning |
|---|---|
| Matrikelnummer / Matr.-Nr. | Student registration number |
| Vorname / Nachname | First name / last name |
| Studiengang | Degree program (referred to as **"course"** in this spec, per the user's terminology) |
| Vers. (Versuch) | Attempt number — which attempt at this exam this is for the student |
| Termin | A specific exam date/sitting within a semester (e.g. "1. Termin", "2. Termin" = a retake date) |
| Kommentar | Free-text status comment from the registration export, e.g. "(angemeldet)" = registered |
| Prüfer | Examiner (instructor) |
| Prüfungsamt | Examination office — the university administration receiving final grades |
| Kombinationsprüfung | A single physical exam sitting that counts as a different official module (different name/credit points/Prüfungsordnung version) in different degree programs |
| n.e. | "nicht erschienen" — did not show up to the exam |
| nicht bestanden | Failed (attended, but score below the passing threshold) |
| WiSe / SoSe | Wintersemester / Sommersemester |

---

## 2. Out of scope (explicitly)

- Creating/authoring the exam questions themselves (done in a separate editor).
- Conducting the exam.
- Correcting/grading individual exam papers (the human does this; the app only records the
  resulting per-exercise point scores).
- Any submission/upload integration with the examination office's IT systems — reports are
  generated as files (PDF/Excel) for the instructor to hand over manually.
- OCR of scanned (non-text) PDFs (see §12, flagged as a known future edge case, not built in v1).

---

## 3. Users & roles

- **Instructor**: an app-level account (username + password). Can create/manage **Lectures** and
  **Exams** they own. Cannot see other instructors' lectures/exams/students.
- **Admin**: a superset role for account management (create/deactivate instructor accounts,
  reset passwords). Whether an admin can also *view* other instructors' exam data for support
  purposes is an open decision — default to **no** (admins manage accounts only, not exam data)
  unless the user says otherwise, since exam data is sensitive personal data and least-privilege
  is the safer default.
- No public/anonymous access, with one exception: an admin can issue a reusable **invitation
  code** that expires after a pre-defined amount of time (default 7 days, configurable
  deployment-wide, not per code). Anyone holding an unexpired, non-revoked, not-yet-exhausted
  code can create their own instructor account with it — always non-admin; granting admin rights
  still requires an existing admin via the account-management API. A code is not consumed by use:
  it can be redeemed by any number of people (e.g. one code posted in a department group chat so
  a whole team can join at once) until it expires, an admin revokes it early, or — optionally —
  it has been redeemed a maximum number of times the admin set when creating it (e.g. "exactly
  the 5 new hires"; unset by default, meaning unlimited). This is an addition to, not a
  replacement for, admin-direct account creation; both ways of creating an instructor account
  stay available side by side. Sharing a code more widely than intended has no backstop besides
  its expiry, manual revocation, and (if set) its use cap, so treat it accordingly. No
  student-facing accounts — students never log in; they only ever see the printed/exported
  "student report" PDF,
  distributed by the instructor through whatever channel is normal in the department (out of
  scope here).
- A second, narrower exception (added post-milestone-6, user request): an instructor can generate
  a **share link** for one exam's §9 statistics dashboard. Anyone holding the link can view that
  exam's live aggregated statistics and use the "what if" bonus-points simulation, without an
  account — but can reach nothing else: no student list, no points entry, no §10/§11 reports (those
  carry names and Matrikelnummern; the §9 dashboard carries only aggregate counts, rates and
  histograms — see §9's own note). The link is a random unguessable token stored on the exam
  (`Exam.share_token`); revoking it, or generating a new one, immediately invalidates the old
  value. This trades a small, accepted re-identification risk (a share link to a 3-student exam
  effectively publishes those 3 students' individual grades within the distribution) for the
  instructor's explicit, revocable choice to publish that aggregate — sharing is off by default
  and opt-in per exam, and the instructor who turns it on controls when it stops.

Passwords are stored using a modern salted hash (e.g. argon2 or bcrypt) — never plaintext.

---

## 4. Core domain model

```
User (instructor or admin)
  └─ Lecture (recurring course, e.g. "Grundlagen der Informationstechnik")
       owner: User
       └─ Exam (one concrete sitting, e.g. WiSe 23/24, 1. Termin)
            semester (e.g. "WiSe 23/24")
            termin (e.g. "1. Termin")
            exam_date
            owner: User (defaults to Lecture's owner, editable)
            grading_schema (per-grade percentage thresholds — see §7)
            bonus_mode: ALWAYS | ONLY_IF_PASSING_WITHOUT_BONUS  (see §7.3)
            bonus_points: decimal (default 0) — one amount, set by the instructor for the whole
              exam and applied identically to every non-excluded student (see §7.3)
            exercises: [ { name, max_points }, ... ]
            └─ StudentRegistration (one per student, imported from PDF or added manually)
                 matrikelnummer, nachname, vorname
                 course_code (Studiengang, short label parsed per source PDF — see §5)
                 module_title (full official module name as printed in that course's source
                   PDF — see §5; may differ per course, e.g. different name/CP/BPO version for
                   a Kombinationsprüfung, even though it's the same physical exam)
                 versuch (attempt number, from PDF)
                 kommentar (raw text from PDF, e.g. "(angemeldet)")
                 flagged: bool  (true if kommentar isn't a normal "registered" status)
                 attended: bool | null   (null = not yet recorded)
                 exercise_points: { exercise_id: points, ... }
                 → computed: raw_total, final_total, grade  (see §7)
```

**New exam creation**: when an instructor creates a new Exam under an existing Lecture, the app
pre-fills `grading_schema`, `bonus_mode`, and the `exercises` list from that Lecture's most
recent prior Exam, as an editable starting point (nothing is locked/inherited live — it's a
one-time copy at creation time). `bonus_points` is **not** copied forward — unlike the other
three, it is this exam's own entered result, not reusable configuration, so a new Exam always
starts at 0.

**Course (Studiengang)** is *not* a separate managed entity in v1 — `course_code` and
`module_title` are both free text captured per imported PDF (see §5). No canonical Studiengang
registry is enforced. Flag this as an assumption: if course-name spelling drifts across
semesters/exports, sorting/grouping in reports will treat differently-spelled variants as
different courses. Revisit if this becomes a problem in practice.

**Lecture name is independent of any PDF's title.** Because the same physical exam can be a
different official module (different name, credit points, Prüfungsordnung version) in each
course, the Lecture entity is purely an internal organizational label the instructor chooses —
it is never auto-derived from, or validated against, the registration PDFs' header text. Its
only job is to group an exam's recurring sittings over time so settings can be copied forward
(see below). `module_title` (per course, from the PDF) is what actually appears in
course-facing report sections — see §10.

**Whole-exam export/import** (added post-milestone-6, by request, not part of the numbered
milestones above): an instructor can download one Exam — its settings, exercises, grading
schema, and every StudentRegistration with its points, excluded ones included — as a single JSON
file, and upload that file back in to recreate it, as a backup or to hand an exam to another
instructor or installation. Import always creates a **new** Exam, never a merge into an existing
one; if the file's Lecture name doesn't match one of the importing instructor's own Lectures, one
is created for it automatically. The importer always becomes the new Exam's owner — the file
never carries an owner identity. See `docs/api-contract.md`'s "Export / import" section for the
exact file shape.

---

## 5. Importing registered students (PDF)

### 5.1 Source format (from the anonymized sample PDF)

The registration export is a text-based (selectable, not scanned) PDF with:

- A header block: export date/time ("Datum/Stand"), semester ("WiSe 23/24"), Termin
  ("Termin: 1. Termin"), a **title line** (e.g. "Grundlagen der Informationstechnik
  (B.Sc. WiIng ET/IT)", or in more elaborate cases something like "Grundlagen der
  Informationstechnik für Wirtschaftsingenieurwesen (B.Sc. WiIng ET/IT), 6 CP,
  BPO 2020/2024 Kombinationsprüfung"), and "Prüfer: ...".
- A table with columns: `Nr.`, `Matr.-Nr.`, `Nachname`, `Vorname`, `Vers.`, `Kommentar`, `Note`
  (Note is empty in the registration export — it exists because this format doubles as the
  office's own working list, but is not populated at import time).

**One PDF = one course (Studiengang).** When a lecture is taken by students from multiple
degree programs, the university exports **one PDF per Studiengang**. So the instructor uploads
one or more PDFs for a single Exam. From each file's title line the app extracts two things:

- `module_title`: the **entire title line, stored verbatim** — this is the official module name
  (plus credit points / Prüfungsordnung version / "Kombinationsprüfung" suffix if present) as
  that specific course knows this exam. Because of Kombinationsprüfung cases, this can differ
  substantially between the PDFs for the same Exam — even naming what is administratively a
  *different module* — and that difference must be preserved, not normalized away.
- `course_code`: the short parenthetical part (e.g. "B.Sc. WiIng ET/IT"), extracted via pattern
  match, used as the short internal label for sorting/grouping/UI display (attendance list,
  filters) — separate from `module_title`, which is what's shown in formal report headers
  (see §10).

Every student row from a given file is tagged with that file's `course_code` and
`module_title`.

### 5.2 Parsing approach

- **Primary**: `pdfplumber` (pure Python, no system dependencies, works fully offline, good
  ruled-table extraction).
- **Fallback**: `PyMuPDF` (`fitz`) as a second extraction engine if pdfplumber fails to find a
  usable table in a given file (registration exports may not be byte-identical across different
  administrative offices/systems — don't hard-code fixed column positions; match columns by
  header text).
- Header metadata (semester, Termin, lecture name, course, Prüfer) parsed via text
  pattern-matching on the page header, not table extraction.
- **Not built in v1**: OCR for scanned/image-based PDFs. If a department's export turns out to
  be a scanned image, import will fail with a clear error. Document this as a known limitation;
  add Tesseract-based OCR later only if it turns out to be needed.
- **Multi-page exports are the normal case, not an edge case.** The real ~50-student exports
  will span multiple pages, each repeating the header block and the table header row
  (`Nr. | Matr.-Nr. | ...`). The parser must extract and concatenate table rows across **all**
  pages of a file, and must recognize and discard repeated table-header rows rather than
  importing them as a (garbage) student row.
- **Test fixture**: `test_data/ZPrf_Grundlagen_der_Informationstechnik_B_Sc_WiIng_ET_IT_WiSe_23_24_TestData.pdf`
  is the anonymized sample to build the parser against and use as an automated test fixture —
  but it only has 2 rows on 1 page, so it does **not** exercise multi-page parsing or the
  row-count checksum below. Construct a larger synthetic multi-page fixture (anonymized data)
  for those tests.

### 5.3 Import validation & merge behavior

When multiple PDFs are uploaded for one Exam:

- `semester` and `termin` parsed from each file should match across all files for that Exam;
  warn (don't hard-block) on mismatch, since it likely means the wrong file was uploaded.
  (`module_title` is deliberately **not** cross-checked for consistency — per §4, it can
  legitimately differ per course for a Kombinationsprüfung.)
- Rows are merged into one student list, each tagged with its source file's `course_code` and
  `module_title`.
- **Duplicate Matrikelnummer across files** (a student appearing in more than one uploaded PDF
  for the same exam) is treated as an import error requiring manual resolution — surfaced to
  the instructor to pick which course/row to keep, never silently merged or duplicated.
- **Kommentar ≠ normal "registered" status** (e.g. not "(angemeldet)"): the row is imported
  and flagged for review in the UI (highlighted), never silently dropped or silently kept —
  the instructor decides per flagged row whether to exclude it before finalizing. "Exclude"
  is an explicit `excluded: bool` state on the `StudentRegistration`, not a deletion: an
  excluded student is kept in the database (so the decision and source data are auditable) but
  is omitted from the attendance list, from points entry, and from every generated report, and
  never receives a grade.
- Instructors can also manually add/edit/remove a student registration after import (e.g. a
  late registration that never appeared in a PDF).
- **Mandatory post-parse validation (per file)** — two *independent* checks, both required:
  1. **Row continuity**: parsed row `Nr.` values must form a contiguous sequence `1..N` with no
     gaps and no duplicates, and `N` must equal the highest parsed `Nr.`.
  2. **Page completeness**: every page the footer declares must be present and parsed — the set
     of printed page numbers must equal `{1..Y}` from `Seite X von Y`, with one consistent `Y`.

  If either check fails — e.g. a page was skipped, a row failed to parse — **hard-fail the
  import** for that file with a clear error naming the missing `Nr.` values and/or page numbers,
  rather than silently importing a partial list. Silently dropping a page (a student never gets
  a grade, with nothing visibly wrong) is the single worst realistic failure mode for this
  feature, so this is mandatory, not a nice-to-have.

  **Check 2 is not redundant with check 1**, which is why both are listed. Losing a page from
  the *middle* leaves a gap in the `Nr.` sequence, so check 1 catches it. Losing the **last**
  page does not: the surviving rows are a flawless contiguous `1..N` with `N` equal to the
  highest `Nr.`, and nothing about the row data itself indicates anything is missing. Only the
  footer knows there should have been another page. (Corrected 2026-07-28: this clause
  previously required `N` to equal "the page footer's declared count … combined with
  rows-per-page". The real export's footer declares *pages*, not rows, and rows-per-page is a
  layout artefact that is neither declared nor constant, so no row total can be derived from it.
  If a future export format does print an explicit participant total, check it as well —
  it would additionally catch a page that is present but only partially parsed.)

---

## 6. Attendance list (print-and-tick report)

- Purpose: printed once before the exam, instructor ticks off attendance by hand.
- Columns: **Studiengang (`course_code`), Matr.-Nr., Nachname, Vorname**.
- Sort: default is by course, then by last name within course, using **German (DIN 5007-1)
  collation** (e.g. "Öztürk" sorts under "O", not after "Z"; ß ≍ ss) — a plain byte/codepoint
  sort visibly mis-orders the printed sheet the instructor physically ticks names off on. Names
  are sorted exactly as printed in the source PDF (e.g. "von Arendelle" sorts under "V", as
  given — no attempt to detect/reorder nobiliary particles). The instructor may instead print in
  one of three alternate orders — by Nachname alone, by Matrikelnummer alone, or by course then
  Matrikelnummer — chosen via a radio selection before download; Nachname-based orders still use
  DIN 5007-1 collation, and Matrikelnummer sorts as the opaque identifier string it is (never
  coerced to a number). This choice affects only the printed row order, never which rows are
  excluded or how the head count is computed.
- One PDF, generated on demand from the current Exam's student list.
- Also used to get the head-count for how many physical exam copies to print (a simple count
  is shown in the UI before/without needing to generate the PDF).

---

## 7. Grading logic

### 7.0 Arithmetic precision (applies to all of §7)

All point/percentage/threshold arithmetic — exercise points, bonus points, percentages,
computed thresholds, and the comparisons that assign a grade — **must use exact decimal
arithmetic** (e.g. Python's `decimal.Decimal`) end to end, never binary floating point.
Grade boundaries are exactly where that kind of error flips a result. Worked example, on a
50-point exam with the 4.0 threshold at 29 %:

| | computation | threshold |
|---|---|---|
| exact (`Decimal`) | `29/100 × 50 = 14.5`, floored to the nearest 0.5 | **14.5** |
| IEEE-754 float | `29/100*50` is `14.499999999999998`, floored to the nearest 0.5 | **14.0** |

Half a point too low, silently — every student between 14.0 and 14.49 passes an exam they
failed. (Corrected 2026-07-28: this paragraph previously cited `0.6 * 45` as evaluating to
`27.000000000000004`. It does not — in CPython `0.6*45`, `45*0.6` and `60/100*45` are all
exactly `27.0`, so that example would have *passed* under a naive float implementation and
argued against the very requirement it was cited for.)

This also constrains storage: SQLite's default `REAL`
affinity is a binary float, so points/percentages must be stored as `TEXT`/`NUMERIC` with
explicit `Decimal` conversion on read/write, not as a bare `REAL` column. An implementing
agent defaulting to plain floats here is the most likely source of silent, hard-to-notice
grading bugs in this app — call this out in code review.

Exercise points are **free decimal entry, not restricted to any fixed step** — e.g. 0.75 points
on an exercise is valid. This is independent of §7.2's threshold rounding, which always rounds
a computed threshold down to the nearest 0.5 regardless of the granularity points happen to be
entered in.

### 7.1 Grade scale

Standard German university scale: **1.0, 1.3, 1.7, 2.0, 2.3, 2.7, 3.0, 3.3, 3.7, 4.0** (passing,
best to worst), anything below the 4.0 threshold is a fail.

### 7.2 Grading schema (per exam, configurable)

For each of the 10 passing grades, the instructor enters a **required percentage of the exam's
total points**. Given the exam's total max points (sum of all exercises' `max_points`), each
grade's point threshold is:

```
threshold_points(grade) = floor( (percentage(grade) / 100 * max_points) / 0.5 ) * 0.5
```

i.e. compute the raw percentage-of-max-points cutoff, then **round down to the nearest 0.5
points**. The app must validate that percentages are strictly decreasing from 1.0 down to 4.0
(each better grade requires a strictly higher percentage than the next) and reject/flag
misconfigured schemas.

A student's grade is the best (numerically lowest) grade whose `threshold_points` is met by
their `final_total`. Below the 4.0 threshold → fail.

### 7.3 Bonus points

There is one `bonus_points` amount per exam (default 0), set by the instructor and applied
identically to every non-excluded student, on top of each student's own sum of exercise points
(`raw_total`). It is not entered per student. Per-exam setting **`bonus_mode`** then governs
whether and how it counts for a given student:

- **ALWAYS**: `final_total = raw_total + bonus_points`, uncapped (may exceed max_points),
  compared directly against thresholds.
- **ONLY_IF_PASSING_WITHOUT_BONUS**: bonus only counts if `raw_total` alone already meets the
  4.0 (passing) threshold. If it does, `final_total = raw_total + bonus_points` (can still
  improve the grade, uncapped). If `raw_total` alone is below the passing threshold, bonus is
  **not** applied — bonus points cannot turn a fail into a pass. (This matches the common German
  "Bonuspunkte können die Note verbessern, aber nicht zum Bestehen führen" rule.)

### 7.4 Attendance interaction

- `attended = false` → grade is **"n.e."**, no points needed/used.
- `attended = true` and `final_total` below the passing (4.0) threshold → grade text is
  **"nicht bestanden"** (not a numeric grade).
- `attended = true` and passing → numeric grade per §7.2.

### 7.5 Worked example (also the acceptance-test spec for §7)

Exam with `max_points = 60`. Grading schema configured with (among others) 1.0 at 95% and
4.0 (pass threshold) at 50%:

| Grade | Percentage | `threshold_points` (floor to nearest 0.5) |
|---|---|---|
| 1.0 | 95% | floor(57.0 / 0.5) × 0.5 = **57.0** |
| 4.0 | 50% | floor(30.0 / 0.5) × 0.5 = **30.0** |

Given these thresholds (`bonus_points` is the exam's one shared value, applied the same way to
every row it appears in):

| `raw_total` | `bonus_points` | `bonus_mode` | `attended` | `final_total` | **Grade** |
|---|---|---|---|---|---|
| 30.0 | 0 | — | true | 30.0 | **4.0** (exactly meets the 4.0 threshold) |
| 29.5 | 0 | — | true | 29.5 | **nicht bestanden** (below 30.0) |
| 29.5 | 0 | — | false | — | **n.e.** (attendance overrides everything) |
| 28.0 | 3 | ALWAYS | true | 31.0 | **4.0** (bonus applied unconditionally, now clears 30.0) |
| 28.0 | 3 | ONLY_IF_PASSING_WITHOUT_BONUS | true | 28.0 | **nicht bestanden** (raw_total 28.0 < 30.0, so bonus is *not* applied) |
| 32.0 | 3 | ONLY_IF_PASSING_WITHOUT_BONUS | true | 35.0 | **3.7 or better per schema** (raw_total 32.0 ≥ 30.0, so bonus *is* applied and can still improve the grade) |

---

## 8. Points/attendance entry (input mask)

- One row per student (grouped/filterable by course), showing `matrikelnummer`, name,
  **`versuch`** (read-only, imported from the PDF — visible here since it's the natural place
  an instructor checking a borderline case wants to see it), and editable fields: attendance
  checkbox, one point-value field per exercise. The exam's single `bonus_points` field is
  entered once for the whole exam (alongside `bonus_mode`), not per row.
- Exercise point totals are shown live per student (sum of entered exercise points), and the
  computed final total + resulting grade update live as values change (server-validated on
  save, not just client-side).
- Points fields accept decimals; validate against each exercise's `max_points` (warn, don't
  silently clamp, if entry exceeds max — typos happen).
- Marking a student as not attended should clear/disable point entry for that student (or at
  least make clear it's irrelevant) rather than requiring zeros to be entered.

### 8.1 Completeness gate & recomputation

- Before an Examination-office or Student-results report (§10, §11) can be generated, the app
  validates that **every non-excluded student** has `attended` recorded, and every
  `attended = true` student has all exercise points entered. If any student is incomplete,
  block export and show the instructor the specific list of incomplete rows — never generate a
  report with implicit zeros or missing grades.
- Editing `max_points` on an exercise, editing the grading schema's percentages, or editing the
  exam's `bonus_points` amount, **after** points have already been entered for some students
  **must trigger a full recomputation** of every affected student's `final_total`/grade, and the
  UI must visibly warn the instructor that this happened (e.g. "Grading schema changed — N
  students' grades were recalculated") — grade thresholds must never shift silently under data
  the instructor may already have transcribed onto paper exams. Since `bonus_points` is now one
  shared amount (§7.3), changing it can move every non-excluded student's grade in a single edit,
  exactly the silent-shift risk this gate exists to catch.

---

## 9. Internal report

Generated per Exam. Two forms, sharing one backend statistics-computation module so numbers
are always consistent between them:

- **PDF** (via Typst): grade distribution summary (count per grade, plus mean and median grade
  among students with a numeric grade), histogram of total-point distribution (1-point-wide
  bins), one histogram per exercise's point distribution (0.5-point-wide bins), attendance rate
  (attended / registered), failure rate (failed / attended), pass rate, and a **pass/fail
  breakdown by `versuch`** (attempt number) — the attempt-tracking requirement's main visible
  use in the app: instructors care most about whether failure rate climbs with attempt number.
  (Bin widths above are a sensible default, not a hard requirement — make them easy to change
  during implementation.)
- **Interactive online version**: the same statistics rendered as an interactive
  page/dashboard within the app itself (charting library in the React frontend, e.g.
  Chart.js/Recharts/Plotly — pick one during implementation), authenticated and visible only to
  the exam's owner (and, per §3, not to admins by default) — **or** to anyone holding that exam's
  optional §3 share link, which unlocks this same read-only dashboard (including the bonus-points
  simulation) without a session, and nothing else. This is a live view over current data, not a
  static export — it reflects entered points immediately, useful while grading is still in
  progress.

Both are for internal use only — never handed to the examination office or students. The dashboard
payload itself (`app/statistics.py::ExamStatistics`) carries no student names or Matrikelnummern —
only aggregate counts, rates, distributions and histograms — which is what makes the §3 share link
safe to hand out without a session: the worst it can expose is small-cohort re-identification
within an aggregate, never a named record.

---

## 10. Examination office report

- Generation is blocked until the §8.1 completeness gate passes for all non-excluded students.
- Generated per Exam.
- Columns: **Matr.-Nr., Nachname, Vorname, Note** (Note = numeric grade, "n.e.", or
  "nicht bestanden", per §7.4).
- Structure: **one section per course (Studiengang)**, students **sorted by Matrikelnummer**
  within each section. **The section heading uses that course's full `module_title`** (the
  verbatim official module name captured from that course's registration PDF, §5.2) — not
  just the short `course_code` — so a Kombinationsprüfung's differing per-course module names,
  credit points, and Prüfungsordnung versions are preserved exactly as the examination office
  would expect to see them for that degree program.
- Formats: **PDF and Excel (.xlsx)**. In the Excel export, include the section's `module_title`
  as a column (or sheet/section label) alongside the four columns above, since a flat sheet
  loses the PDF's visual section grouping.
- Kept deliberately simple (not mirroring the original registration-PDF layout) per explicit
  decision — revisit only if the examination office says otherwise.

---

## 11. Student results report

- Generation is blocked until the §8.1 completeness gate passes for all non-excluded students.
- Generated per Exam.
- Columns: **Matr.-Nr., Note** only (no names — matches common practice of posting anonymized
  grade lists).
- Sort: by Matrikelnummer only (no course grouping).
- Formats: **PDF and Excel (.xlsx)**.

---

## 12. Technology stack

| Concern | Choice | Why |
|---|---|---|
| Backend | Python + FastAPI | Best-in-class offline PDF/data tooling (see below); async-friendly API for the React frontend. |
| Frontend | React | Real interactivity needed for input mask (live totals/grades) and the interactive internal-report dashboard. |
| Database | SQLite | Zero-admin, single-file, trivial to back up; comfortably handles the expected scale (dozens–low hundreds of students per exam, single-digit concurrent instructor users). |
| Auth | App-level accounts, password hashing (argon2/bcrypt) | No department SSO integration assumed available; simple to self-contain. |
| Registration-PDF parsing | `pdfplumber` primary, `PyMuPDF` fallback | Pure/near-pure Python, no heavy system dependencies (no Ghostscript/JVM like camelot/tabula-py), fully offline, good ruled-table extraction, actively maintained. |
| Report generation | Typst, via the `typst-py` binding | Single static binary, no TeXLive install, fast, clean templating from JSON-like data, native table support, full UTF-8/German support. |
| Charts in PDFs | Typst `cetz` + `cetz-plot` packages, **vendored into the Docker image at build time** | These packages are normally fetched from Typst's `@preview` registry over the network on first use — vendoring avoids any runtime network dependency, satisfying the no-internet-at-runtime constraint. |
| Excel export | `openpyxl` (or similar) | Standard, offline, no extra system dependencies. |

**Why not LaTeX**: viable alternative (very mature, excellent German typography), but heavier
to deploy (full TeXLive), slower to compile, and more awkward to template from a Python backend
than Typst's JSON-driven approach. Explicitly deprioritized per the user's choice of Typst.

---

## 13. Deployment

- Generic self-hosted **Linux + Docker** (docker-compose), reachable only within the
  department network — no public internet exposure.
- Assumption: TLS termination and network access restriction are handled by an existing
  department reverse proxy / firewall in front of the container; this app serves plain HTTP
  internally. Confirm with department IT before going live.
- The Docker image must be self-contained at runtime: Python deps, the `typst` toolchain,
  local fonts (e.g. Noto Sans/Serif for German umlauts), and vendored `cetz`/`cetz-plot`
  packages all baked in at build time — no outbound network calls needed to generate a report.
- SQLite database file lives on a persistent volume. **Backup strategy is not specified in this
  document** — recommend the department set up simple periodic file-level backups (e.g. cron +
  `sqlite3 .backup`, or a tool like Litestream) of the volume; decide and document separately.
- **Data retention**: no automatic deletion. Instructors are responsible for deleting an exam
  (and all its personal data) once retention obligations expire; the app must provide an
  explicit "delete exam" action that cascades to all student registrations/points for that exam.

---

## 14. Open assumptions / risks to confirm during implementation

These were either explicitly deferred by the user or are reasonable defaults chosen to keep
this spec unblocked — flag any of these back to the user if they turn out to matter:

1. **Registration PDF layout variability**: only one real sample was available. Other
   departments'/systems' exports may differ in column order/header wording — the parser should
   match columns by header text, not fixed position, and fail loudly (not silently
   misimport) on an unrecognized layout.
2. **Scanned/image PDFs**: not supported in v1; OCR (Tesseract, fully offline once installed)
   is the documented fallback path if this turns out to be needed.
3. **Course (Studiengang) as free text**: no canonical list/normalization in v1; drift in
   naming across semesters could fragment grouping in reports.
4. ~~Points entry granularity~~ — **confirmed**: free decimal entry (e.g. 0.75-point exercises
   are valid), independent of grade *thresholds*, which are always rounded down to 0.5 per §7.2
   (exact `Decimal` arithmetic throughout, per §7.0).
5. **Admin role scope**: assumed account-management only, no visibility into other instructors'
   exam data. Confirm this is the desired privacy boundary.
6. **Number/date formatting**: reports should use German conventions (comma decimal separator,
   e.g. "1,3"; DD.MM.YYYY dates) — not yet explicitly confirmed by the user, but treated as a
   safe default.
7. **Examination-office report format**: user chose the simple 4-column version over mirroring
   the original registration-PDF layout, but flagged this is worth double-checking with the
   Prüfungsamt before relying on it for a real submission.
8. **SQLite backup strategy**: intentionally left to department ops, not app functionality.
9. **`module_title` capture**: assumed to be the entire PDF title line, stored verbatim per
   course with no normalization/validation across a Kombinationsprüfung's differing per-course
   names. If a source PDF's title line ever wraps across the header in a way that makes
   verbatim capture ambiguous, this needs a closer look during implementation against real
   (non-anonymized) samples from other lectures.

---

## 15. Suggested build order

1. Data model, auth (accounts + admin role), Lecture/Exam CRUD.
2. Registration-PDF import → attendance list PDF export (validates the parsing approach early,
   the highest-uncertainty piece; build the multi-page synthetic fixture per §5.2 and test the
   §5.3 row-count checksum here, not later).
3. Points/attendance entry UI + grading-schema configuration + grade computation engine
   (with unit tests around §7's rounding/bonus rules using the §7.5 worked example as the test
   cases — these are easy to get subtly wrong, and easy to get subtly wrong *silently* if
   `Decimal` arithmetic per §7.0 isn't used from the start).
4. Internal report: shared statistics module → PDF export + interactive dashboard.
5. Examination-office report and student-results report (PDF + Excel).
6. Exam-settings copy-forward when creating a new Exam under an existing Lecture; explicit
   exam-deletion function; deployment packaging (Dockerfile/compose, vendored Typst packages).
