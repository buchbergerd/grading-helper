// Notenliste (SPECIFICATION.md §11) — the student-results grade report.
//
// Same convention as attendance_list.typ/examination_office.typ: the whole document is driven by
// one JSON blob passed through `sys.inputs.data` by app/reports/student_results.py. No sorting,
// no filtering and no formatting decisions happen here — rows already arrive in §11's order and
// `note` is already German-formatted ("1,3" / "nicht bestanden" / "n.e.", app/reports/grades.py).
//
// Deliberately NO names anywhere in this template's data or output (§11: "no names — matches
// common practice of posting anonymized grade lists") and NO `#import "@preview/..."` of any kind
// (§13: network-fetched at runtime). This report needs no charts, so a plain Typst table suffices.
//
// Expected `data` shape (see StudentResultsData in app/reports/student_results.py):
//   { lecture_name: str, semester: str, termin: str, exam_date: str|none,
//     rows: [{matrikelnummer, note}] }

#let data = json(bytes(sys.inputs.at("data")))

#set page(
  paper: "a4",
  margin: (top: 1.8cm, bottom: 1.6cm, left: 1.6cm, right: 1.4cm),
  footer: context align(center)[
    #set text(size: 8.5pt)
    Seite #counter(page).display("1") von #counter(page).final().first()
  ],
)
#set text(size: 10pt, lang: "de")

// --- Kopfblock ------------------------------------------------------------------------------
#align(center)[
  #text(size: 17pt, weight: "bold")[Notenliste] \
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
  ..info-row("Datum:", if data.exam_date == none { box(width: 4cm, line(length: 100%)) } else {
    data.exam_date
  }),
)
#v(10pt)

// --- Tabelle ----------------------------------------------------------------------------------
// One flat table, no course/module grouping (§11: "Sort: by Matrikelnummer only (no course
// grouping)"), and no name columns at all.
#if data.rows.len() == 0 [
  #v(8pt)
  #emph[Für diese Prüfung sind keine Studierenden angemeldet.]
] else [
  // Fixed-width columns, not `1fr`: a two-column Matr.-Nr./Note table stretched to the full page
  // width leaves a wide empty gap between the columns and wastes paper on a list that can run to
  // hundreds of rows (§12: "dozens-low hundreds of students per exam"). Centering the narrow
  // table instead keeps it compact and readable, closer to how a posted grade list actually looks.
  #align(center)[
    #table(
      columns: (5.5cm, 3cm),
      align: (left, right),
      inset: (x: 6pt, y: 5.5pt),
      stroke: 0.4pt + luma(120),
      fill: (_, y) => if y == 0 { luma(225) } else if calc.even(y) { luma(246) },
      table.header(
        [*Matr.-Nr.*],
        [*Note*],
      ),
      ..data.rows
        .map(r => (
          [#r.matrikelnummer],
          [#r.note],
        ))
        .flatten(),
    )
  ]
]
