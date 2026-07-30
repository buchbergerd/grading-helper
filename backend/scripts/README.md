# Backend scripts

## `generate_demo_data.py`

Fills an exam already sitting in the local dev database (e.g. one created through the UI and
imported from a registration PDF) with random attendance, per-exercise points and bonus points,
so the points grid, statistics dashboard and reports have something realistic to show.

```
cd backend
uv run python scripts/generate_demo_data.py            # the one exam in the DB
uv run python scripts/generate_demo_data.py --exam-id 1 --seed 42   # pick one, reproducibly
```

Deliberately not deterministic by default and not a `test_data/` fixture: it mutates whatever
local dev database `app/config.py` points at, in place, and nothing it produces is committed.
Mostly marks students attended with plausible per-student point totals, but always leaves a few
rows incomplete (unattended, attendance not yet recorded, or missing one exercise) and lets a
few scores exceed `max_points` — the §8.1 completeness gate and the over-max warning path both
need something to actually catch, not an artificially complete dataset.

## `create_admin.py`

Creates an admin account (SPECIFICATION.md §3). See the module docstring for usage.

## `make_fixtures.py`

Generates the anonymized synthetic registration-PDF fixtures required by SPECIFICATION.md §5.2:
the one real sample in `/test_data/` has only 2 rows on 1 page, so it can't exercise multi-page
parsing or the §5.3 row-count checksum. Renders via Typst (the `typst` Python binding, already a
project dependency for §12 report rendering — this script doubles as an early smoke test of that
toolchain).

```
cd backend
uv run python scripts/make_fixtures.py
```

Writes five PDFs into `/test_data/`, all committed to the repo (see the root `.gitignore`'s
`test_data/*_synthetic*.pdf` allowlist and `/test_data/README.md`) so tests never depend on Typst
being installed — `backend/tests/test_fixtures_are_wellformed.py` reads the committed files
directly with pdfplumber and doesn't import this script.

| File | What it exercises |
| --- | --- |
| `registration_synthetic_multipage.pdf` | Normal case: ~50 students, 3 pages, header block + table header repeated per page, `Nr.` continuous 1–50 across pages. Includes an umlaut ("Öztürk"), a nobiliary particle + double given name ("von Arendelle", "Leyla Olivia"), an "ß" name ("Groß") — all relevant to the §6 German (DIN 5007-1) sort — and 3 rows with a non-`(angemeldet)` Kommentar for the §5.3 review-flag path. |
| `registration_synthetic_second_course.pdf` | A second Studiengang for the *same* exam sitting (same semester/Termin), with a different, longer Kombinationsprüfung-style `module_title`/`course_code` — exercises that §5.1 requires this difference to be preserved, never normalized. |
| `registration_synthetic_duplicate_matrikelnummer.pdf` | One row shares a Matr.-Nr. with a row in `registration_synthetic_multipage.pdf` — §5.3 requires this to be a hard import error requiring manual resolution. |
| `registration_synthetic_broken_gap.pdf` | `Nr.` sequence skips a value (17 → 19) — simulates a row that failed to parse; §5.3 requires a hard import failure. |
| `registration_synthetic_broken_missing_page.pdf` | Footer declares "Seite 1 von 3" / "Seite 3 von 3" but page 2 was never rendered — simulates a dropped page; §5.3 requires a hard import failure. |

### Design notes

- **No real student data.** Names are drawn from fairy tales/folklore ("Rotkäppchen",
  "Rumpelstilzchen", "von Münchhausen", ...) or the standard German placeholder names
  ("Mustermann", "Musterfrau", "Beispiel"). Matrikelnummern use a `999xxxx` block (seven digits,
  always starting `999`) — not a range any real university issues, so a stray fixture file is
  unmistakable at a glance.
- **Structurally close to the real export, but not byte-identical in layout.** The table is
  rendered with the same 9 logical/physical columns `pdfplumber.extract_tables()` reports for the
  real sample — `['Nr.', 'Matr.-Nr.', '', 'Nachname', '', 'Vorname', 'Vers.', 'Kommentar',
  'Note']`, including the two empty interleaved columns — so a parser that locates columns by
  header text (per §5.2) behaves identically on both. `backend/tests/test_fixtures_are_wellformed.py`
  proves this by running the same header-matching helper against the real sample and every
  synthetic fixture. Known differences from the real PDF, none of which weaken what §5.2 actually
  requires (header-text column matching), but worth knowing before hand-tuning a parser against
  either file specifically:
  - **Cell rendering.** The real export draws each table cell as a filled, differently-shaded
    rect (no ruled lines); our Typst table uses plain 0.5pt ruled strokes instead. One visible
    side effect: the real sample's page also contains a couple of empty decorative `[['']]`
    pseudo-tables ahead of the real one (pdfplumber's table-detection picking up letterhead
    graphics drawn the same way as the cells) — the synthetic fixtures don't produce those. A
    real parser has to skip stray tables without a `Nr.` header either way.
  - **Header block typography/spacing.** The real sample's `WiSe`/`Termin`/title/`Prüfer` lines
    measure ~12pt and the `Datum:`/`Stand:` line ~6.5pt (from `pdfplumber`'s word `height`); the
    synthetic fixtures use a uniform 10pt for all header lines (matching the table body), and a
    plain `#v(26pt)` gap stands in for whatever letterhead/logo graphic produces the real
    sample's larger gap between the Datum line and the semester line — nothing is drawn there.
  - **fitz/PyMuPDF text order.** Under `fitz.Page.get_text()`, the real sample's filled-rect
    table comes back with cells regrouped roughly *column-major* — e.g. all `Nr.` values, then
    all `Matr.-Nr.` values, interleaved with unrelated fragments — not in reading order. The
    synthetic fixtures' ruled Typst table comes back far more orderly (each row's cells stay
    together, field per line). A fitz-based fallback parser built and tested only against the
    synthetic fixtures could therefore be surprised by the real file's shape; this was not
    something this script could practically reproduce (it follows from the university's export
    tool's underlying PDF content-stream ordering, not from anything under Typst's control here).
- **Deterministic and byte-reproducible.** Student data is a small literal/derived table (no
  `random`), and the Typst compile is pinned to a fixed `timestamp` — without it, Typst embeds
  the wall-clock compile time and every run's PDF bytes would differ for no reason. Verified:
  regenerating produces byte-identical files to what's committed (given the same Typst version
  and system fonts as this toolchain — Typst 0.15, Liberation Sans).
- **Long title lines shrink to fit, they don't wrap or run off the page.**
  `registration_synthetic_second_course.pdf`'s Kombinationsprüfung-style title is 131 characters
  and doesn't fit the ~499pt printable width at the default 10pt, so it's rendered smaller
  (`Fixture.module_title_font_size`) rather than wrapped onto a second line. An earlier version
  of this script instead widened the containing box past the page edge to force one line, which
  looked fine under pdfplumber (reads the raw content stream) but silently truncated the title
  under PyMuPDF/`fitz` (clips to the page mediabox — this is §5.2's named fallback parser).
  `backend/tests/test_fixtures_are_wellformed.py::test_second_course_title_survives_the_pymupdf_fallback_parser`
  guards against that regression specifically.
