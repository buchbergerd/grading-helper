"""Generate anonymized synthetic registration-PDF fixtures (SPECIFICATION.md §5.2).

    uv run python scripts/make_fixtures.py

The one real sample fixture in ``test_data/`` has only 2 rows on 1 page, so it cannot exercise
multi-page parsing or the §5.3 row-count checksum. This script renders a handful of additional
fixtures via Typst that reproduce the real export's layout closely enough that a parser matching
table columns *by header text* (per §5.2) works unmodified on both:

- a "normal" ~50-student, 3-page registration list,
- a second course (different Studiengang) for the *same* exam sitting, to exercise multi-file
  merge behaviour (§5.1/§5.3),
- a file that duplicates a Matrikelnummer already used in the first fixture (§5.3 hard error),
- a file whose ``Nr.`` sequence has a gap, simulating a row that failed to parse (§5.3 hard
  error), and
- a file whose declared page count doesn't match the pages actually present, simulating a
  dropped page (§5.3 hard error).

**No real student data anywhere in this file.** Names are drawn from fairy tales/folklore
("Rotkäppchen", "Rumpelstilzchen", "von Münchhausen", ...) or are the standard German
placeholder names ("Mustermann", "Musterfrau", "Beispiel") so nothing here could be mistaken for
a real registration export. Matrikelnummern use a ``999xxxx`` block — seven digits starting with
``999`` is not a range this university (or any other) issues, so a stray fixture file is
unmistakable at a glance.

Output is deterministic: the student data is a literal/derived table (no ``random``), and the
Typst compile is pinned to a fixed ``timestamp`` so regenerating produces byte-identical PDFs
(verified in the toolchain: two compiles of the same source, in two separate processes, produced
identical bytes once ``timestamp`` was pinned — Typst otherwise embeds the wall-clock time and
each run differs).

The five PDFs this script writes are committed to the repo (see ``test_data/README.md`` and the
root ``.gitignore``'s ``test_data/*_synthetic*.pdf`` allowlist) so tests don't depend on a Typst
toolchain being available; ``backend/tests/test_fixtures_are_wellformed.py`` checks the committed
files directly.
"""

from __future__ import annotations

import datetime
import sys
from dataclasses import dataclass, field
from pathlib import Path

# Run as a plain file (`python scripts/make_fixtures.py`), sys.path[0] is scripts/ and `app` is
# not importable as a package-relative import from here. We don't actually import `app`, but
# keep the same bootstrap convention as the other scripts in this directory for consistency.
if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import typst

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
TEST_DATA_DIR = REPO_ROOT / "test_data"

# Pinned so Typst doesn't embed the wall-clock compile time, which would make regenerated PDFs
# byte-differ from the committed ones for no reason other than "when did someone last run this".
FIXED_TIMESTAMP = datetime.datetime(2024, 1, 22, 9, 50, 53, tzinfo=datetime.UTC)

FONT = "Liberation Sans"

# Same header text as the real sample (test_data/ZPrf_..._TestData.pdf) — only the semester/date
# are shared across all fixtures below; title/Prüfer/Termin vary per fixture.
DATUM_STAND = "Datum: 22.01.2024, Stand: 09:50:53 Uhr"
SEMESTER = "WiSe 23/24"
TERMIN = "Termin: 1. Termin"
PRUEFER = "Prüfer: Prof.Dr.-Ing. Armin Dekorsy"
MODULE_TITLE_1 = "Grundlagen der Informationstechnik (B.Sc. WiIng ET/IT)"
MODULE_TITLE_2 = (
    "Grundlagen der Informationstechnik für Wirtschaftsingenieurwesen "
    "(B.Sc. WiIng ET/IT M.Sc.), 6 CP, BPO 2020/2024 Kombinationsprüfung"
)


@dataclass(frozen=True)
class Student:
    nr: int
    matrikel: int
    nachname: str
    vorname: str
    vers: int
    kommentar: str = "(angemeldet)"


@dataclass(frozen=True)
class Page:
    footer: str
    students: list[Student]


@dataclass(frozen=True)
class Fixture:
    filename: str
    module_title: str
    pruefer: str = PRUEFER
    termin: str = TERMIN
    semester: str = SEMESTER
    pages: list[Page] = field(default_factory=list)
    #: Font size (pt) for the module_title header line only. The default (10pt, matching the
    #: table body text) is fine for short titles; a long Kombinationsprüfung-style title (see
    #: MODULE_TITLE_2) needs a smaller size to still fit on one physical text line within the
    #: printable width — see the comment on `_one_line` for why "one line" matters here.
    module_title_font_size: float = 10.0


# --- Deterministic fictional name pools -------------------------------------------------------
# Fairy-tale/folklore figures and the standard German "placeholder person" names. Deliberately
# nothing that could be mistaken for a real surname roster.
SURNAMES = [
    "Mustermann",
    "Musterfrau",
    "Beispiel",
    "Nachtigall",
    "Sonnenschein",
    "Winterfeld",
    "Mondschein",
    "Wolkenstein",
    "Rotkäppchen",
    "Schneewittchen",
    "Dornröschen",
    "Rumpelstilzchen",
    "von Münchhausen",
    "Nordwind",
    "Sagenhaft",
]
GIVEN_NAMES = [
    "Max",
    "Erika",
    "Bob",
    "Theo",
    "Lotte",
    "Peter",
    "Nora",
    "Fritz",
    "Rosa",
    "Karl",
    "Aurora",
    "Ferdinand",
    "Isegrim",
    "Balu",
    "Mina",
]


def _pool_name(index: int) -> tuple[str, str]:
    """A deterministic (Nachname, Vorname) pair for a given 0-based index — no randomness."""
    nachname = SURNAMES[index % len(SURNAMES)]
    vorname = GIVEN_NAMES[(index * 4 + 2) % len(GIVEN_NAMES)]
    return nachname, vorname


def _make_students(
    start_nr: int,
    count: int,
    matrikel_base: int,
    overrides: dict[int, tuple[str, str, str]] | None = None,
) -> list[Student]:
    """``count`` students numbered ``start_nr..start_nr+count-1``, cycling the name pools.

    ``overrides`` maps a 0-based row index (within this call) to ``(nachname, vorname,
    kommentar)``, used to force in the specific special-case rows each fixture needs (umlaut,
    nobiliary particle, double given name, "ß", non-"(angemeldet)" Kommentar, ...).
    """
    overrides = overrides or {}
    students = []
    for i in range(count):
        nachname, vorname = _pool_name(i)
        kommentar = "(angemeldet)"
        if i in overrides:
            nachname, vorname, kommentar = overrides[i]
        students.append(
            Student(
                nr=start_nr + i,
                matrikel=matrikel_base + i,
                nachname=nachname,
                vorname=vorname,
                vers=1 + (i % 3),
                kommentar=kommentar,
            )
        )
    return students


def _chunk(students: list[Student], sizes: list[int]) -> list[list[Student]]:
    assert sum(sizes) == len(students), (sum(sizes), len(students))
    chunks = []
    start = 0
    for size in sizes:
        chunks.append(students[start : start + size])
        start += size
    return chunks


# --- Fixture 1: normal multi-page case ---------------------------------------------------------
# 50 students across 3 pages (18/18/14), Nr. continuous 1..50. Special rows required by the task:
#   idx 3  (Nr 4)  -> nobiliary particle + double given name ("von Arendelle", "Leyla Olivia")
#                     — reused verbatim from the real sample's row 2, which already uses this
#                     exact fictional name.
#   idx 4  (Nr 5)  -> umlaut that matters for §6 German (DIN 5007-1) collation ("Öztürk")
#   idx 5  (Nr 6)  -> "ß" ("Groß")
#   idx 9  (Nr 10), idx 24 (Nr 25), idx 39 (Nr 40) -> Kommentar != "(angemeldet)", for the §5.3
#     review-flag test. Kept short so they don't wrap the Kommentar column.
_FIXTURE_1_OVERRIDES = {
    3: ("von Arendelle", "Leyla Olivia", "(angemeldet)"),
    4: ("Öztürk", "Aylin", "(angemeldet)"),
    5: ("Groß", "Fritz", "(angemeldet)"),
    9: (*_pool_name(9), "(zurückgetreten)"),
    24: (*_pool_name(24), "(krank gemeldet)"),
    39: (*_pool_name(39), "(exmatrikuliert)"),
}
FIXTURE_1_STUDENTS = _make_students(
    start_nr=1, count=50, matrikel_base=9990001, overrides=_FIXTURE_1_OVERRIDES
)
_f1_chunks = _chunk(FIXTURE_1_STUDENTS, [18, 18, 14])
FIXTURE_1 = Fixture(
    filename="registration_synthetic_multipage.pdf",
    module_title=MODULE_TITLE_1,
    pages=[
        Page(footer=f"Seite {i + 1} von {len(_f1_chunks)}", students=chunk)
        for i, chunk in enumerate(_f1_chunks)
    ],
)

# --- Fixture 2: second course, same exam sitting (Kombinationsprüfung) -------------------------
# Same semester/Termin as fixture 1 (same physical exam), different module_title/course_code
# (§5.1: this difference must be preserved, never normalized). 15 students, 1 page. Matrikel
# block disjoint from fixture 1/3/4/5 — this is the "clean" multi-file merge case, so it must
# NOT accidentally collide with fixture 1.
FIXTURE_2_STUDENTS = _make_students(start_nr=1, count=15, matrikel_base=9991001)
FIXTURE_2 = Fixture(
    filename="registration_synthetic_second_course.pdf",
    module_title=MODULE_TITLE_2,
    # MODULE_TITLE_2 is 131 characters — doesn't fit the ~499pt printable width on one line at
    # the default 10pt (confirmed empirically: 8.5pt still wraps, 8pt doesn't). A shrunk-to-fit
    # title line is also just realistic: a real export tool is more likely to autosize an
    # overlong title field than to either wrap it or run it off the page.
    module_title_font_size=8.0,
    pages=[Page(footer="Seite 1 von 1", students=FIXTURE_2_STUDENTS)],
)

# --- Fixture 3: duplicate Matrikelnummer across files -------------------------------------------
# 10 students, 1 page, matrikel block 9992xxx *except* row index 6 (Nr 7), which reuses fixture
# 1's row idx 4 (Nr 5, "Öztürk, Aylin", Matr.-Nr. 9990005) verbatim — a student who appears to be
# registered for this exam under two different Studiengänge, which §5.3 requires the import to
# flag as an error requiring manual resolution rather than silently merge or duplicate.
_fixture_3_pre = _make_students(start_nr=1, count=10, matrikel_base=9992001)
_dup_source = FIXTURE_1_STUDENTS[4]
assert _dup_source.matrikel == 9990005 and _dup_source.nachname == "Öztürk"
_fixture_3_students = [
    Student(
        nr=s.nr,
        matrikel=_dup_source.matrikel if s.nr == 7 else s.matrikel,
        nachname=_dup_source.nachname if s.nr == 7 else s.nachname,
        vorname=_dup_source.vorname if s.nr == 7 else s.vorname,
        vers=s.vers,
        kommentar=s.kommentar,
    )
    for s in _fixture_3_pre
]
FIXTURE_3 = Fixture(
    filename="registration_synthetic_duplicate_matrikelnummer.pdf",
    module_title=MODULE_TITLE_1,
    pages=[Page(footer="Seite 1 von 1", students=_fixture_3_students)],
)

# --- Fixture 4: Nr. gap (a row that failed to parse) --------------------------------------------
# 20 physical rows on 1 page, but numbered 1..17, 19..21 — Nr. 18 never appears. §5.3 requires a
# hard import failure since the parsed Nr. sequence isn't contiguous 1..N.
_fixture_4_students = _make_students(start_nr=1, count=17, matrikel_base=9993001) + [
    Student(nr=nr, matrikel=9993000 + nr, nachname=n, vorname=v, vers=1 + (nr % 3))
    for nr, (n, v) in zip(
        (19, 20, 21), (_pool_name(17), _pool_name(18), _pool_name(19)), strict=True
    )
]
FIXTURE_4 = Fixture(
    filename="registration_synthetic_broken_gap.pdf",
    module_title=MODULE_TITLE_1,
    pages=[Page(footer="Seite 1 von 1", students=_fixture_4_students)],
)

# --- Fixture 5: missing page (footer says "von 3", only 2 pages present) -----------------------
# Conceptually a 30-student, 3-page list (10 rows/page) like fixture 1, but the middle page (Nr.
# 11..20) is simply never rendered — only the physical pages labelled "Seite 1 von 3" and
# "Seite 3 von 3" are emitted. This is both a dropped page (§5.3 hard failure) *and*, as a side
# effect, a Nr. gap (11..20) — which is exactly what a dropped page looks like to the parser.
_fixture_5_page1 = _make_students(start_nr=1, count=10, matrikel_base=9994001)
_fixture_5_page3 = _make_students(start_nr=21, count=10, matrikel_base=9994021)
FIXTURE_5 = Fixture(
    filename="registration_synthetic_broken_missing_page.pdf",
    module_title=MODULE_TITLE_1,
    pages=[
        Page(footer="Seite 1 von 3", students=_fixture_5_page1),
        Page(footer="Seite 3 von 3", students=_fixture_5_page3),
    ],
)

ALL_FIXTURES = [FIXTURE_1, FIXTURE_2, FIXTURE_3, FIXTURE_4, FIXTURE_5]


# --- Typst rendering -----------------------------------------------------------------------------
# Column layout mirrors the real sample's `extract_tables()` shape *exactly*: pdfplumber reports
# ['Nr.', 'Matr.-Nr.', '', 'Nachname', '', 'Vorname', 'Vers.', 'Kommentar', 'Note'] for the real
# PDF — 9 columns, with empty columns interleaved after Matr.-Nr. and Nachname. Reproducing that
# (rather than a "tidier" 7-column table) means a parser that locates columns by header text
# behaves identically on both, and a header/column-index mismatch can't hide in the fixtures.
_TABLE_COLUMNS = "26pt, 66pt, 4pt, 1fr, 4pt, 1fr, 30pt, 100pt, 36pt"
_TABLE_ALIGN = "right, left, left, left, left, left, right, left, left"
_HEADER_CELLS = [
    "*Nr.*",
    "*Matr.-Nr.*",
    "",
    "*Nachname*",
    "",
    "*Vorname*",
    "*Vers.*",
    "*Kommentar*",
    "*Note*",
]


def _typst_escape(text: str) -> str:
    """Escape Typst markup-mode special characters in interpolated plain text.

    None of the literal data in this file actually contains any of these (German names/titles
    use only letters, spaces, hyphens, dots, commas, parentheses, ß and umlauts) but this is
    cheap insurance against a future edit introducing one silently breaking the layout.
    """
    special = "\\#*_`$<>[]@"
    return "".join("\\" + c if c in special else c for c in text)


def _one_line(alignment: str, text: str, size_pt: float = 10.0) -> str:
    """Render ``text`` as a single, on-page Typst text line — never word-wrapped.

    A field wrapping onto a second line would break the "5-line header block, repeated on every
    page" structure this generator promises (and ``_verify_compiled`` below enforces). An
    earlier version of this function forced a single line by boxing the text far wider than the
    page and letting it overflow past the margins — that "worked" for pdfplumber (which reads
    the content stream regardless of the visible/cropped region) but silently truncated the text
    when read with PyMuPDF/fitz (§5.2's fallback parser), which clips to the page's mediabox. Do
    not reintroduce that: it makes fitz-based parsing invisibly wrong on a fixture whose whole
    job is catching that kind of thing.

    Instead, the caller is responsible for choosing a ``size_pt`` small enough that the text
    actually fits the ~499pt printable width without wrapping — see
    ``Fixture.module_title_font_size`` for the one header field long enough to need this
    (``MODULE_TITLE_2``). ``_verify_compiled`` still asserts the resulting page has exactly 5
    header lines, so a size that's still too large fails loudly at generation time rather than
    silently producing a fixture that doesn't test what it claims to.
    """
    escaped = _typst_escape(text)
    return f"#align({alignment})[#text(size: {size_pt}pt)[{escaped}]]"


def _page_typst(
    page: Page,
    module_title: str,
    pruefer: str,
    termin: str,
    semester: str,
    module_title_font_size: float,
) -> str:
    cells = list(_HEADER_CELLS)
    for s in page.students:
        cells += [
            str(s.nr),
            str(s.matrikel),
            "",
            _typst_escape(s.nachname),
            "",
            _typst_escape(s.vorname),
            str(s.vers),
            _typst_escape(s.kommentar),
            "",
        ]
    cell_blocks = ",\n  ".join(f"[{c}]" for c in cells)
    return f"""#set page(footer: align(left)[{_typst_escape(page.footer)}])
{_one_line("right", DATUM_STAND)}
#v(26pt)
{_one_line("center", semester)}
#v(4pt)
{_one_line("center", termin)}
#v(4pt)
{_one_line("center", module_title, size_pt=module_title_font_size)}
#v(4pt)
{_one_line("center", pruefer)}
#v(2pt)
#table(
  columns: ({_TABLE_COLUMNS}),
  stroke: 0.5pt,
  inset: 5pt,
  align: ({_TABLE_ALIGN}),
  {cell_blocks}
)
"""


def render_typst(fixture: Fixture) -> str:
    doc = (
        '#set page(paper: "a4", margin: (left: 71pt, right: 25pt, top: 35pt, bottom: 40pt))\n'
        f'#set text(font: "{FONT}", size: 10pt)\n'
    )
    for i, page in enumerate(fixture.pages):
        if i > 0:
            doc += "#pagebreak(weak: true)\n"
        doc += _page_typst(
            page,
            fixture.module_title,
            fixture.pruefer,
            fixture.termin,
            fixture.semester,
            fixture.module_title_font_size,
        )
    return doc


def _verify_compiled(fixture: Fixture, pdf_path: Path) -> None:
    """Sanity-check the just-compiled PDF against what we asked Typst to render.

    Guards against silent layout drift (e.g. a name wrapping to a second line, which would
    change row heights and could push content onto an unplanned extra page) — if this fires,
    the fixture no longer tests what its docstring above says it tests.
    """
    import pdfplumber

    with pdfplumber.open(pdf_path) as pdf:
        if len(pdf.pages) != len(fixture.pages):
            raise RuntimeError(
                f"{fixture.filename}: expected {len(fixture.pages)} physical page(s), "
                f"got {len(pdf.pages)} — a row likely wrapped onto an extra page"
            )
        for i, (page, expected) in enumerate(zip(pdf.pages, fixture.pages, strict=True)):
            text = page.extract_text() or ""
            lines = text.splitlines()
            # 5 header lines + 1 table-header line + N data rows + 1 footer line.
            expected_line_count = 5 + 1 + len(expected.students) + 1
            if len(lines) != expected_line_count:
                raise RuntimeError(
                    f"{fixture.filename} page {i + 1}: expected {expected_line_count} text "
                    f"lines, got {len(lines)} — a cell likely wrapped:\n{text}"
                )
            if lines[-1] != expected.footer:
                raise RuntimeError(
                    f"{fixture.filename} page {i + 1}: footer mismatch: "
                    f"{lines[-1]!r} != {expected.footer!r}"
                )


def build_fixture(fixture: Fixture, out_dir: Path) -> Path:
    source = render_typst(fixture)
    typ_path = out_dir / f"{fixture.filename.removesuffix('.pdf')}.typ"
    typ_path.write_text(source, encoding="utf-8")
    pdf_path = out_dir / fixture.filename
    typst.compile(str(typ_path), output=str(pdf_path), timestamp=FIXED_TIMESTAMP)
    typ_path.unlink()  # intermediate source, not part of the committed fixture
    _verify_compiled(fixture, pdf_path)
    return pdf_path


def main() -> int:
    TEST_DATA_DIR.mkdir(parents=True, exist_ok=True)
    print(f"Writing synthetic fixtures to {TEST_DATA_DIR}\n")
    for fixture in ALL_FIXTURES:
        pdf_path = build_fixture(fixture, TEST_DATA_DIR)
        n_pages = len(fixture.pages)
        n_rows = sum(len(p.students) for p in fixture.pages)
        size = pdf_path.stat().st_size
        print(f"  {pdf_path.name:55s} {n_pages} page(s), {n_rows:3d} row(s), {size:6d} bytes")
    print("\nDone.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
