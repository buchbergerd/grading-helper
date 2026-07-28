// Anwesenheitsliste (SPECIFICATION.md §6) — the print-and-tick attendance sheet.
//
// This template renders *only* what it is handed: the whole document is driven by one JSON blob
// passed through `sys.inputs.data` by app/reports/attendance_list.py (SPECIFICATION.md §12 picks
// Typst precisely for this "clean templating from JSON-like data" property). It therefore
// contains no sorting, no filtering and no formatting decisions — the row order is already the
// §6 DIN 5007-1 order, excluded students are already gone, and `exam_date` already arrives as a
// German DD.MM.YYYY string. Keep it that way: sorting logic in a Typst template would be
// untestable from pytest.
//
// Deliberately NO `#import "@preview/..."` of any kind. Those are fetched from Typst's package
// registry over the network on first use, which SPECIFICATION.md §13 forbids at runtime. This
// report needs no charts, so plain Typst suffices; no font is selected either, so rendering uses
// the fonts embedded in the typst binary and works with `ignore_system_fonts=True`.
//
// Expected `data` shape (see AttendanceListData in app/reports/attendance_list.py):
//   { lecture_name: str, semester: str, termin: str, exam_date: str|none, head_count: int,
//     courses: [{course_code: str, count: int}], students: [{course_code, matrikelnummer,
//     nachname, vorname}] }

#let data = json(bytes(sys.inputs.at("data")))

#set page(
  paper: "a4",
  margin: (top: 1.8cm, bottom: 1.6cm, left: 1.6cm, right: 1.4cm),
  // "Seite N von M" is built by hand rather than with `#set page(numbering: "Seite 1 von 1")`:
  // a numbering *pattern* treats the letters i/I/a/A as counting symbols, so the literal word
  // "Seite" would render as "Seiite" on page 2. Do not "simplify" this back to a pattern.
  footer: context align(center)[
    #set text(size: 8.5pt)
    Seite #counter(page).display("1") von #counter(page).final().first()
  ],
)
#set text(size: 10pt, lang: "de")

// --- Kopfblock ------------------------------------------------------------------------------
#align(center)[
  #text(size: 17pt, weight: "bold")[Anwesenheitsliste] \
  #v(1pt)
  #text(size: 12pt)[#data.lecture_name]
]
#v(8pt)

#let info-row(label, value) = ([#text(weight: "bold")[#label]], [#value])
#grid(
  columns: (auto, 1fr),
  column-gutter: 10pt,
  row-gutter: 3.5pt,
  ..info-row("Semester:", data.semester),
  ..info-row("Termin:", data.termin),
  // No exam_date recorded: print a rule to fill in by hand rather than hiding the field — the
  // sheet is a paper document that should still be complete once the date is known.
  ..info-row("Datum:", if data.exam_date == none { box(width: 4cm, line(length: 100%)) } else {
    data.exam_date
  }),
  ..info-row(
    "Anzahl Studierende:",
    [#data.head_count#if data.courses.len() > 1 [
        #h(4pt) (#data.courses.map(c => c.course_code + ": " + str(c.count)).join(", "))
      ]],
  ),
)
#v(10pt)

// --- Tabelle --------------------------------------------------------------------------------
// One continuous table with a visible Studiengang column (§6's first column) rather than a page
// break per course: the sheet is ticked off in one pass and the running Nr. must stay continuous
// so it doubles as the head count for how many exam copies to print.
#let tick-box = align(center)[#box(
    width: 10pt,
    height: 10pt,
    stroke: 0.7pt + black,
    radius: 1pt,
  )]

#table(
  columns: (auto, auto, auto, 1fr, 1fr, auto),
  align: (right, left, left, left, left, center),
  inset: (x: 6pt, y: 5.5pt),
  stroke: 0.4pt + luma(120),
  fill: (_, y) => if y == 0 { luma(225) } else if calc.even(y) { luma(246) },
  table.header(
    [*Nr.*],
    [*Studiengang*],
    [*Matr.-Nr.*],
    [*Nachname*],
    [*Vorname*],
    [*Anwesend*],
  ),
  ..data.students
    .enumerate(start: 1)
    .map(((i, s)) => (
      [#i],
      [#s.course_code],
      [#s.matrikelnummer],
      [#s.nachname],
      [#s.vorname],
      tick-box,
    ))
    .flatten(),
)

#if data.students.len() == 0 [
  #v(8pt)
  #emph[Für diese Prüfung sind keine Studierenden angemeldet.]
]

#v(14pt)
#grid(
  columns: (1fr, 1fr),
  column-gutter: 20pt,
  [#line(length: 100%, stroke: 0.5pt) #text(size: 8.5pt)[Ort, Datum]],
  [#line(length: 100%, stroke: 0.5pt) #text(size: 8.5pt)[Unterschrift Aufsicht]],
)
