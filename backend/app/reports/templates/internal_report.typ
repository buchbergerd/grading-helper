// Interner Bericht (SPECIFICATION.md §9) — the instructor-only statistics PDF.
//
// Like attendance_list.typ, this template renders *only* what it is handed: the whole document
// is driven by one JSON blob passed through `sys.inputs.data` by app/reports/internal_report.py.
// The producing side of that blob is app/statistics.py's `build_exam_statistics`, which §9
// requires to be the *single* source of these numbers so the PDF and the in-app dashboard can
// never disagree. That only holds if this template computes nothing of its own: every count,
// every percentage, every histogram bin label and every rounding decision already happened in
// Python. This file's only jobs are (1) laying the numbers out on a page and (2) drawing bars —
// it appends a "%" sign and static German words, and swaps a decimal point for a comma (see `de`
// below, which is the sole exception and is a character swap, not arithmetic). Do not let a
// "quick" percentage or a mean/median get computed here even for a display-only tweak; add it to
// app/statistics.py instead, or the dashboard and the PDF *will* eventually show two different
// numbers for the same exam, which is the exact failure §9's "one module" requirement prevents.
//
// Deliberately NO `#import "@preview/..."` of any kind — same reasoning as attendance_list.typ:
// those packages are fetched from Typst's package registry over the network on first use, which
// SPECIFICATION.md §13 forbids at runtime. §12 names `cetz`/`cetz-plot` as the intended charting
// packages, vendored at Docker build time — that is §15.6, a later milestone. Until then, every
// bar in this report is drawn with plain Typst primitives (`rect`, `grid`, `stack`) behind the
// one `bar-chart` function below, so swapping in cetz-plot later is a change to that one
// function and nothing else.
//
// Expected `data` shape: ExamStatistics, see app/statistics.py. Every field there has a docstring
// explaining exactly what it means and why it is shaped the way it is; read that file, not this
// comment, for the contract.

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
#set heading(numbering: none)
#show heading.where(level: 1): it => [
  #v(10pt)
  #text(size: 12.5pt, weight: "bold")[#it.body]
  #v(3pt)
]

// --- Kleine Formatierungshelfer ---------------------------------------------------------------
// Every one of these takes an already-finished value from `data` and inserts, at most, a literal
// "%" or a static German word — see this file's header comment. None of them divide, round or
// compare two payload numbers against each other.

#let em-dash = [—]

// The decimal separator, and the *only* transform this template applies to a payload number.
//
// Payload decimals are canonical strings with a period ("84.6", "2.35"), because that is how
// every decimal crosses this app's HTTP boundary (CLAUDE.md, §7.0) — `app/statistics.py` feeds
// one payload to both this PDF and the JSON API, so it cannot German-format them at the source
// without breaking the frontend's `isCanonicalDecimal`/`formatDecimal` contract. §14 #6 requires
// a comma in everything user-facing, so each renderer swaps the separator on the way to the
// screen: `frontend/src/util/format.ts::formatDecimal` there, this function here.
//
// This is display transliteration, not computation — a deterministic character swap that cannot
// round, re-scale or reorder anything, so the two views still cannot disagree about a number.
// Nothing beyond this may be done to a payload value here; see the header comment.
//
// Composite captions are NOT built this way: a histogram bin's `label` arrives already assembled
// and already German ("12,0–13,0") precisely because composing it from two edges is the kind of
// thing two renderers could do differently. Do not pass a `label` through `de`.
#let de(value) = str(value).replace(".", ",")

// §9: a Rate never gets divided in a renderer — `percent` is already a rounded string (or
// `none`, rendered as an em dash), and `numerator`/`denominator` are printed alongside it exactly
// as the payload carries them, e.g. "84,6 % (33 von 39)".
#let rate-text(rate) = {
  let pct = if rate.percent == none { em-dash } else { [#de(rate.percent) %] }
  [#pct (#str(rate.numerator) von #str(rate.denominator))]
}

#let info-row(label, value) = ([#text(weight: "bold")[#label]], [#value])

#let kennzahl(label, value) = block(width: 100%, below: 4pt)[
  #grid(
    columns: (7.2cm, 1fr),
    [#label], [#value],
  )
]

// --- Balkendiagramm ----------------------------------------------------------------------------
// The one and only place bars get drawn (see header comment). `entries` is a list of
// `(label, count)` pairs — the exact shape produced by mapping over a payload's `bins` or grade
// list. Bars are horizontal: with up to ~40 bins in a total-points histogram, a horizontal layout
// keeps every bar the same, legible height and keeps the chart's *width* fixed at the page width
// regardless of how many bins there are — a vertical layout would instead squeeze every bar
// thinner as bins are added, which is exactly the overflow this function must avoid. Label text
// is a bin's `label` string, printed verbatim (already German-formatted, per the payload).
#let bar-track-width = 8.5cm
#let bar-row-height = 8pt

#let bar-chart(title: "", entries: (), note: none) = block(width: 100%, below: 6pt)[
  #if title != "" [
    #text(weight: "bold", size: 10pt)[#title]
    #v(3pt)
  ]
  #let has-data = entries.len() > 0 and entries.any(e => e.at(1) > 0)
  #if not has-data [
    #emph[keine Daten]
  ] else {
    let max-count = calc.max(..entries.map(e => e.at(1)))
    grid(
      columns: (3.8cm, bar-track-width, auto),
      column-gutter: 5pt,
      row-gutter: 2.5pt,
      align: (right + horizon, left + horizon, left + horizon),
      ..entries
        .map(((label, count)) => {
          let filled = if max-count == 0 { 0pt } else {
            (count / max-count) * bar-track-width
          }
          // A non-zero count that would round to an invisible sliver still gets a visible tick —
          // "0 bars drawn" must never be confused with "0 counted", which the number to the right
          // of the bar already disambiguates, but a visible tick makes the chart itself honest too.
          let filled = if count > 0 and filled < 1.5pt { 1.5pt } else { filled }
          (
            text(size: 7.5pt)[#label],
            stack(
              dir: ltr,
              rect(width: filled, height: bar-row-height, fill: luma(60), stroke: none),
              rect(
                width: bar-track-width - filled,
                height: bar-row-height,
                fill: luma(232),
                stroke: none,
              ),
            ),
            text(size: 7.5pt)[#count],
          )
        })
        .flatten(),
    )
  }
  #if note != none [
    #v(4pt)
    #text(size: 8pt, style: "italic")[#note]
  ]
]

// --- Kopfblock ----------------------------------------------------------------------------------
#align(center)[
  #text(size: 17pt, weight: "bold")[Interner Bericht]
  \
  #v(1pt)
  #text(size: 12pt)[#data.lecture_name]
]
#v(8pt)

#grid(
  columns: (auto, 1fr),
  column-gutter: 10pt,
  row-gutter: 3.5pt,
  ..info-row("Semester:", data.semester),
  ..info-row("Termin:", data.termin),
  ..info-row("Datum:", if data.exam_date == none { em-dash } else { data.exam_date }),
  ..info-row("Erstellt am:", data.generated_at),
)
#v(6pt)
#block(
  width: 100%,
  fill: luma(235),
  inset: 7pt,
  radius: 2pt,
)[
  #text(weight: "bold")[Nur für den internen Gebrauch.]
  Dieser Bericht wird nicht an das Prüfungsamt oder an Studierende weitergegeben (§9).
]

// --- Status: laufende Bewertung / kein Notenschema --------------------------------------------
// §9: this PDF must never be mistakable for a final result while grading is in progress, or
// while no grading schema has been configured at all. Both states get a plain, unmissable line —
// simple integer addition of two already-computed counts, not a statistic of its own.
#let not-yet-included = data.counts.incomplete + data.counts.attendance_not_recorded
#if not data.grading_configured or not-yet-included > 0 [
  #block(
    width: 100%,
    fill: luma(220),
    stroke: 0.6pt + luma(90),
    inset: 7pt,
    radius: 2pt,
  )[
    #if not data.grading_configured [
      #text(weight: "bold")[Kein Notenschema konfiguriert.]
      Für diese Prüfung ist noch kein vollständiges Notenschema hinterlegt; es konnten keine
      Noten berechnet werden. #linebreak()
    ]
    #if not-yet-included > 0 [
      #text(weight: "bold")[Bewertung noch nicht abgeschlossen.]
      #str(not-yet-included) Studierende(r) (unvollständig bewertet oder Anwesenheit nicht
      erfasst) #if not-yet-included == 1 { "ist" } else { "sind" } noch nicht in der
      Notenverteilung und im Histogramm der Gesamtpunkte berücksichtigt.
    ]
  ]
]

// --- Kennzahlen ----------------------------------------------------------------------------------
= Kennzahlen

#grid(
  columns: (1fr, 1fr),
  column-gutter: 16pt,
  [
    #kennzahl("Angemeldet:", str(data.counts.registered))
    #kennzahl("Anwesend:", str(data.counts.attended))
    #kennzahl("Nicht anwesend:", str(data.counts.not_attended))
    #kennzahl("Anwesenheit nicht erfasst:", str(data.counts.attendance_not_recorded))
  ],
  [
    #kennzahl("Bewertet:", str(data.counts.graded))
    #kennzahl("Unvollständig:", str(data.counts.incomplete))
    #kennzahl("Bestanden:", str(data.counts.passed))
    #kennzahl("Nicht bestanden:", str(data.counts.failed))
  ],
)
#v(4pt)
#kennzahl("Anwesenheitsquote:", rate-text(data.rates.attendance))
#kennzahl("Bestehensquote:", rate-text(data.rates.passing))
#kennzahl("Durchfallquote:", rate-text(data.rates.failure))

// --- Notenverteilung -----------------------------------------------------------------------------
= Notenverteilung

// Grade labels are decimals too ("1.3" → "1,3", per §14 #6's "1,3" example). The two text
// categories below are deliberately *not* passed through `de`: "n.e." contains a period that is
// an abbreviation mark, not a decimal separator, and swapping it would print "n,e,".
#let grade-rows = data.grade_distribution.numeric.map(g => (de(g.grade), str(g.count)))
#let grade-rows = grade-rows + (
  ("nicht bestanden", str(data.grade_distribution.failed_count)),
  ("n.e.", str(data.grade_distribution.not_attended_count)),
)

#table(
  columns: (auto, auto),
  align: (left, right),
  inset: (x: 6pt, y: 4pt),
  stroke: 0.4pt + luma(120),
  fill: (_, y) => if y == 0 { luma(225) } else if calc.even(y) { luma(246) },
  table.header([*Note*], [*Anzahl*]),
  ..grade-rows.map(((grade, count)) => ([#grade], [#count])).flatten(),
)
#v(4pt)
#let numeric-count = data.grade_distribution.numeric_count
#kennzahl(
  [Mittelwert (über #str(numeric-count) Studierende mit Note):],
  if data.grade_distribution.mean == none { em-dash } else { de(data.grade_distribution.mean) },
)
#kennzahl(
  [Median (über #str(numeric-count) Studierende mit Note):],
  if data.grade_distribution.median == none { em-dash } else { de(data.grade_distribution.median) },
)
#v(4pt)
// No `title:` here — the "Notenverteilung" heading directly above already names this chart;
// repeating it as the bar-chart's own title would just print the same word twice in a row.
#bar-chart(entries: grade-rows.map(((grade, count)) => (grade, int(count))))

// --- Histogramm der Gesamtpunkte -----------------------------------------------------------------
= Histogramm der Gesamtpunkte

#let total-hist = data.total_points_histogram
#bar-chart(
  title: total-hist.title,
  entries: total-hist.bins.map(b => (b.label, b.count)),
  note: [
    Bezugsgröße (max. Punktzahl): #de(total-hist.reference_max) ·
    höchster erfasster Wert: #if total-hist.max_observed == none { em-dash } else {
      de(total-hist.max_observed)
    } ·
    berücksichtigte Studierende: #str(total-hist.included_count)
  ],
)

// --- Histogramme je Aufgabe ------------------------------------------------------------------
= Histogramme je Aufgabe

#if data.exercise_histograms.len() == 0 [
  #emph[Für diese Prüfung sind keine Aufgaben konfiguriert.]
] else {
  for hist in data.exercise_histograms [
    #bar-chart(
      title: hist.title,
      entries: hist.bins.map(b => (b.label, b.count)),
      note: [
        Bezugsgröße (max. Punktzahl): #de(hist.reference_max) ·
        höchster erfasster Wert: #if hist.max_observed == none { em-dash } else {
          de(hist.max_observed)
        } ·
        berücksichtigte Studierende: #str(hist.included_count)
      ],
    )
  ]
}

// --- Bestehensquote nach Versuch ---------------------------------------------------------------
= Bestehensquote nach Versuch

#table(
  columns: (auto, auto, auto, auto, auto, 1fr),
  align: (left, right, right, right, right, left),
  inset: (x: 6pt, y: 4.5pt),
  stroke: 0.4pt + luma(120),
  fill: (_, y) => if y == 0 { luma(225) } else if calc.even(y) { luma(246) },
  table.header(
    [*Versuch*],
    [*Angemeldet*],
    [*Bewertet*],
    [*Bestanden*],
    [*Nicht bestanden*],
    [*Durchfallquote*],
  ),
  ..data.versuch_breakdown
    .map(v => (
      [#v.label],
      [#str(v.registered)],
      [#str(v.graded)],
      [#str(v.passed)],
      [#str(v.failed)],
      [#rate-text(v.failure_rate)],
    ))
    .flatten(),
)
#if data.versuch_breakdown.len() == 0 [
  #v(6pt)
  #emph[Keine Daten.]
]
