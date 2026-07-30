// Prüfungsamt-Bericht (SPECIFICATION.md §10) — the examination-office grade report.
//
// Same convention as attendance_list.typ: the whole document is driven by one JSON blob passed
// through `sys.inputs.data` by app/reports/examination_office.py. No sorting, no filtering and no
// formatting decisions happen here — sections/rows already arrive in §10's order and `note` is
// already German-formatted ("1,3" / "nicht bestanden" / "n.e.", app/reports/grades.py).
//
// Deliberately NO `#import "@preview/..."` of any kind (§13: network-fetched at runtime). This
// report needs no charts, so plain Typst tables suffice.
//
// Expected `data` shape (see ExaminationOfficeData in app/reports/examination_office.py):
//   { lecture_name: str, semester: str, termin: str, exam_date: str|none,
//     sections: [{course_code: str, module_title: str,
//                 rows: [{matrikelnummer, nachname, vorname, note}]}] }

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
  #text(size: 17pt, weight: "bold")[Prüfungsamt-Bericht] \
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

// --- Abschnitte -------------------------------------------------------------------------------
// One section per (course_code, module_title) pair, headed by the full module_title — a
// Kombinationsprüfung legitimately shows two sections with the same course_code but different
// module_title, so the heading must be the verbatim module_title, not just the short code.
#if data.sections.len() == 0 [
  #v(8pt)
  #emph[Für diese Prüfung sind keine Studierenden angemeldet.]
]

#for section in data.sections [
  #v(10pt)
  #block(breakable: false)[
    #text(size: 12.5pt, weight: "bold")[#section.module_title]
    #h(6pt)
    #text(size: 9pt, fill: luma(90))[(#section.course_code)]
  ]
  #v(4pt)
  #table(
    columns: (auto, 1fr, 1fr, auto),
    align: (left, left, left, right),
    inset: (x: 6pt, y: 5.5pt),
    stroke: 0.4pt + luma(120),
    fill: (_, y) => if y == 0 { luma(225) } else if calc.even(y) { luma(246) },
    table.header(
      [*Matr.-Nr.*],
      [*Nachname*],
      [*Vorname*],
      [*Note*],
    ),
    ..section.rows
      .map(r => (
        [#r.matrikelnummer],
        [#r.nachname],
        [#r.vorname],
        [#r.note],
      ))
      .flatten(),
  )
]
