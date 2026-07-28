# Open questions / assumptions register

Tracks `SPECIFICATION.md` §14 as living items, plus any new ones found during implementation.
Update the **Status** column as items get resolved — don't let this rot into a copy of the spec.

| # | Question | Default assumption in spec | Status |
|---|---|---|---|
| 1 | Registration PDF layout variability across departments/systems | Match columns by header text, not fixed position; fail loudly on unrecognized layout | Open |
| 2 | Scanned/image PDFs (no OCR in v1) | Fail with clear error; Tesseract OCR is the documented future fallback | Open |
| 3 | Course (Studiengang) as free text, no canonical registry | Accept drift risk in v1; revisit if grouping fragments in practice | Open |
| 4 | Points entry granularity | **Confirmed**: free decimal entry, independent of §7.2 threshold rounding | Resolved |
| 5 | Admin role scope (account mgmt only, no exam-data visibility) | Default to no visibility for admins (least privilege) | Open — confirm with user |
| 6 | Number/date formatting in reports | German conventions: comma decimal separator ("1,3"), DD.MM.YYYY | Open — treated as safe default |
| 7 | Examination-office report format (simple 4-column vs. mirroring original PDF) | User chose simple version | Open — double-check with Prüfungsamt before real submission |
| 8 | SQLite backup strategy | Left to department ops (cron + `sqlite3 .backup`, or Litestream) | Open — not app functionality |
| 9 | `module_title` capture when a source PDF's title line wraps | Assumed verbatim capture of the whole title line is unambiguous | **Resolved (2026-07-28)**: a real anonymized export (`..._BPO_2020_2024_SoSe_26_TestData.pdf`) wraps its title across two physical lines. The region between `Termin:` and `Prüfer:` *is* the title by construction, so all its lines are rejoined with a single space — reconstruction, not guesswork. Remaining gap: a wrap that **hyphenates** a word would yield `"Wirtschaftsingenieur- wesen"`; no observed export hyphenates, and blind de-hyphenation would corrupt a legitimately hyphenated title (`BPO 2020-` / `2024`). Revisit only with a sample that exhibits it |
| 10 | DIN 5007-1 German collation implementation choice for the attendance-list sort (§6) | Not specified in spec; candidates were a pure-Python table (`pyuca`) vs. system ICU bindings (`PyICU`, needs `libicu` at build time) | **Resolved (M2)**: neither — a stdlib-only sort key in `backend/app/collation.py` (expand `ß→ss` etc., NFD-decompose, drop combining marks, casefold, original as tiebreaker). `PyICU` would pull `libicu` into the image against §12/§13's no-heavy-system-dependency stance; `pyuca` is a large DUCET table for a small, well-defined transform. No new dependency |
| 11 | Schema migrations (Alembic) | Not in the spec. M1 uses `Base.metadata.create_all()`; models still churn through M1–M5 and there is no deployed data yet | Deferred — generate the initial Alembic migration at §15.6 (deployment packaging), before any real deployment |
| 12 | Session lifetime | Not in the spec. Chose 12 h absolute expiry on a DB-backed session token in an HttpOnly cookie (DB-backed, not JWT, so §3's account deactivation / password reset revokes immediately) | Open — trivially configurable via `GRADINGHELPER_SESSION_LIFETIME_HOURS`; confirm the duration suits department practice |
| 13 | Password policy | Not in the spec (§3 only mandates a salted hash). Chose a 12-character minimum with no composition rules — accounts are admin-created (§3), there is no public signup, and composition rules mostly produce predictable substitutions. Enforced on admin create/reset, self-service change and `scripts/create_admin.py` | Open — confirm the minimum length suits department password practice |
| 14 | Response shape for password-policy failures | Not in `docs/api-contract.md`, which only specifies `422` + `{"detail": {"errors": [...]}}` for grading-schema/exercise validation. Reused that shape so the frontend has one German-message renderer | Open — fold into the contract if it stays |

| 15 | §5.3's row-count checksum had no declared row total to check against | §5.3 originally asked that `N` equal "the page footer's declared count … combined with rows-per-page". The real export's footer declares **pages**, not rows, and rows-per-page is an undeclared layout artefact | **Resolved (2026-07-28)**: user confirmed the spec clause was in error; §5.3 has been rewritten to require two independent checks — `Nr.` contiguous `1..N`, **and** every footer-declared page present. If an export format ever prints an explicit participant total, add it as a third check |

Item 10 was added during environment setup (not in the original spec §14) — it surfaced while
choosing backend dependencies for `backend/pyproject.toml`. Items 11–14 were added during
milestone 1 (§15.1), item 15 during milestone 2 (§15.2) on first inspecting the real sample PDF.
