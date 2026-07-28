# Scripts

## `generate_synthetic_registration_pdf.py` (not yet written)

SPECIFICATION.md §5.2 requires a synthetic, anonymized, **multi-page** registration PDF fixture
to test multi-page parsing and the §5.3 row-count checksum — the real sample fixture in
`/test_data/` only has 2 rows on 1 page and doesn't exercise either.

When written, this script should:

- Generate a PDF with the same header block + table structure as the real sample (semester,
  Termin, title line, `Prüfer:`, and the `Nr. | Matr.-Nr. | Nachname | Vorname | Vers. |
  Kommentar | Note` table), repeating the header and table-header row on every page like the
  real export does.
- Use fully fictitious names/Matrikelnummern (no real student data anywhere in this repo).
- Produce enough rows to span at least 3 pages, including at least one non-"(angemeldet)"
  Kommentar (for §5.3 flagging tests) and a page-footer count field consistent with the total
  row count.
- Write output to `test_data/` with a `_synthetic` suffix (see root `.gitignore`, which
  otherwise excludes stray PDFs from that directory).
