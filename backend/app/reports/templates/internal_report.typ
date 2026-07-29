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
// Bars are drawn with `cetz` + `cetz-plot` (§12), vendored at build time into
// app/reports/typst_packages/ by scripts/vendor_typst_packages.py — see
// app/reports/internal_report.py's docstring for why importing them here stays §13-compliant
// (the fetch happens once, at build time, on a machine with no exam data on it yet; the render
// itself, with `package_path` set, never touches the network). The versions imported below
// (`cetz:0.3.4`, `cetz-plot:0.1.1`) must always match exactly what is vendored — see
// tests/test_internal_report.py's `test_template_imports_match_the_vendored_package_versions`.
//
// Only `bar-chart` (plus its small `versuch-chart` sibling for the grouped attempt breakdown)
// draws bars, so swapping the charting library again later is a change to those two functions
// and nothing else. Bar *geometry* — scaling a count to a pixel height, spacing bars, thinning
// which x tick labels get drawn — is not a reported number and is exactly what a plotting
// package is for; it is not covered by the "compute nothing" rule above. A histogram bin's
// `label` is still printed verbatim from the payload as each tick's text, never rebuilt from
// `lower`/`upper`.
//
// Expected `data` shape: ExamStatistics, see app/statistics.py. Every field there has a docstring
// explaining exactly what it means and why it is shaped the way it is; read that file, not this
// comment, for the contract.

#import "@preview/cetz:0.3.4"
#import "@preview/cetz-plot:0.1.1": plot

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
// and already German ("[12;13[") precisely because composing it from two edges is the kind of
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

// --- Balkendiagramme -----------------------------------------------------------------------
// The only place bars get drawn (see header comment). Colours are the app's accent plus two
// tones reserved for the Notenverteilung's non-numeric categories — chosen to stay distinct in
// greyscale too (a mid red and a mid grey against the dark blue).
#let accent = rgb("#1c4f8b")
#let color-failed = rgb("#b0334a")
#let color-absent = rgb("#8a8a8a")

// Fixed regardless of bin count (CLAUDE.md: bar geometry is not a reported number) — this is
// what keeps the chart's *width* constant on the page no matter how many bars it holds; cetz-plot
// scales bar spacing to fit, rather than the old hand-drawn version's bars growing thinner as
// more were added. Kept modest in height so a heading+chart unit (see `chart-section` below)
// still leaves room for other content on the same page instead of forcing a near-empty page.
#let chart-size = (15.5, 4.4)

// Above this many entries, x tick labels start colliding, so only every nth one is drawn (every
// *bar* still gets drawn — see `bar-chart` below). 12 categories (Notenverteilung, §9's own
// count: ten grades plus "nicht bestanden" and "n.e.") must always fit whole, so this must never
// go below 12.
#let max-x-labels = 12

// entries: array of (label, count) or (label, count, color) tuples. `color` is the fallback for
// a plain 2-tuple; a 3-tuple's own colour wins, letting one chart mix colours per category
// (Notenverteilung's "nicht bestanden"/"n.e." vs. the ten numeric grades).
#let entry-color(entry, fallback) = if entry.len() > 2 { entry.at(2) } else { fallback }

// A "nice" whole-number y-axis tick step for a given max count. Cosmetic axis scaling only — not
// a reported number (CLAUDE.md: bar geometry is fine) — but a plain `auto` step can pick a
// half-integer spacing for a small max count, which prints a fractional tick label for a series
// that is always a whole head count. Also sidesteps a cetz-plot quirk where an `auto` y-max
// combined with a fixed `y-tick-step` can silently drop one tick label near the origin.
#let nice-y-step(max-count) = {
  if max-count <= 10 { 1 }
  else if max-count <= 20 { 2 }
  else if max-count <= 50 { 5 }
  else if max-count <= 100 { 10 }
  else { calc.ceil(max-count / 10) }
}

// The last shown x tick can otherwise sit close enough to the x-axis's arrowhead/label to touch
// it once there are many bars — nudging its content a couple of points left (via invisible
// spacing, not a position change) keeps it legible without shrinking the plotted domain.
#let end-nudge(body) = box[#body#h(2pt)]

// Bar *positions* run 1..n, not 0..n-1, with the x domain explicitly padded a half-unit past
// either end (`x-min: 0.5`, `x-max: n + 0.5`). school-book axis-style always crosses the two axes
// at (0, 0) and merely *clamps* that crossing point into the plotted domain — with 0-indexed
// positions, 0 sits inside the domain, at the first bar's own centre, so the y-axis was drawn
// straight through (or just past) the middle of the first bar rather than at the chart's left
// edge. That was invisible with dozens of thin bins (the offset is a tiny fraction of the total
// width) but glaring with only two or three wide categories (the Versuch chart) — the axis
// visibly cut through the first group. Starting positions at 1 puts the default crossing (0)
// safely outside the domain, so clamping pins it exactly at `x-min`, i.e. the left edge, for
// every chart, wide or narrow.
#let first-position = 1

// §14 #6 / this file's header comment: "Punkte"/"Note"/"Versuch" on the x-axis is not a reported
// number, but it was colliding with the last tick label often enough (any histogram whose last
// bin reaches the arrowhead) to be worth just dropping — the section heading directly above
// every chart, plus the unit already printed in each bin's own label, make an axis title
// redundant here. `y-label` keeps "Anzahl" since nothing else on the page states what the bars
// count.
#let no-x-label = none

// `breakable: false`: a `block` is breakable by default, and a cetz `canvas` does not degrade
// gracefully when Typst tries to split one mid-drawing across a page boundary — the axis
// arrowhead and label can end up sliced off entirely rather than the chart simply moving to the
// next page. Forcing the whole title+chart+note unit to move together is what makes the "page-
// break sensibly" requirement hold for a chart that doesn't fit in the remaining space. The
// *heading* above a chart is bundled into this same non-breakable unit by `chart-section` below,
// not by this function — a heading is never orphaned from the chart it names.
#let bar-chart(title: "", entries: (), note: none, color: accent) = block(
  width: 100%,
  below: 6pt,
  breakable: false,
)[
  #if title != "" [
    #text(weight: "bold", size: 10pt)[#title]
    #v(3pt)
  ]
  #let has-data = entries.len() > 0 and entries.any(e => e.at(1) > 0)
  #if not has-data [
    #emph[keine Daten]
  ] else {
    let n = entries.len()
    let last = n - 1
    let step = calc.max(1, calc.ceil(n / max-x-labels))
    let tick-indices = range(0, n).filter(i => calc.rem(i, step) == 0)
    // The last bin's label is always forced in (tests rely on it, and an instructor should
    // always be able to read the top of the range) — but if `step` doesn't divide evenly, the
    // naive append can land it closer than `step` to the previous shown tick, close enough for
    // the two labels to overlap. Swap out that previous tick instead of stacking both.
    if last not in tick-indices {
      if tick-indices.len() > 0 and (last - tick-indices.last()) < step {
        tick-indices = tick-indices.slice(0, tick-indices.len() - 1) + (last,)
      } else {
        tick-indices = tick-indices + (last,)
      }
    }
    let x-ticks = tick-indices.map(i => {
      let label-content = text(size: 6.5pt)[#entries.at(i).at(0)]
      let content = if i == last { end-nudge(label-content) } else { label-content }
      (i + first-position, content)
    })

    // add-bar applies one uniform style per call, so mixed colours mean one call per contiguous
    // run of the same colour rather than one call for the whole series — the runs are always
    // contiguous in every payload this template renders (all-numeric, then "nicht bestanden",
    // then "n.e."), so this never fragments a single-colour series into more than one call.
    let groups = ()
    let current-color = none
    let current-list = ()
    for (i, e) in entries.enumerate() {
      let c = entry-color(e, color)
      if current-color == none {
        current-color = c
      } else if c != current-color {
        groups.push((current-color, current-list))
        current-color = c
        current-list = ()
      }
      current-list.push((i + first-position, e.at(1)))
    }
    groups.push((current-color, current-list))
    let max-count = calc.max(1, ..entries.map(e => e.at(1)))

    cetz.canvas({
      import cetz.draw: *
      // Draw the y-axis's own "0" in its normal spot instead of the combined origin glyph
      // school-book style draws by default — that combined glyph sits exactly where the first x
      // tick's label would also be centred, so with more than a handful of bars it collided with
      // whatever the first bar's label was.
      set-style(axes: (shared-zero: false))
      plot.plot(
        size: chart-size,
        x-tick-step: none,
        x-ticks: x-ticks,
        x-min: first-position - 0.5,
        x-max: first-position + n - 1 + 0.5,
        x-label: no-x-label,
        y-label: "Anzahl",
        y-min: 0,
        y-tick-step: nice-y-step(max-count),
        y-decimals: 0,
        axis-style: "school-book",
        {
          for (bar-color, pairs) in groups {
            plot.add-bar(pairs, bar-width: 0.85, style: (fill: bar-color, stroke: none))
          }
        },
      )
    })
  }
  #if note != none [
    #v(4pt)
    #text(size: 8pt, style: "italic")[#note]
  ]
]

// The one grouped chart: bestanden/nicht bestanden counts side by side per Versuch. Two plain
// `add-bar` calls at x-offsets `±0.22` around each attempt's integer position stand in for
// cetz-plot's "clustered" mode, which applies one style to the whole call and so cannot give the
// two bars of a cluster different colours on its own.
#let versuch-chart(groups) = block(width: 100%, below: 6pt, breakable: false)[
  #if groups.len() == 0 [
    #emph[keine Daten]
  ] else {
    let n = groups.len()
    let last = n - 1
    // Positions 1..n, same reasoning as `bar-chart` above: keeps the default (0, 0) axis
    // crossing clamped to the left edge instead of straddling the first group, which was
    // dramatic here with only two or three wide categories.
    let passed-data = groups.enumerate().map(((i, g)) => (i + first-position - 0.22, g.passed))
    let failed-data = groups.enumerate().map(((i, g)) => (i + first-position + 0.22, g.failed))
    let x-ticks = groups.enumerate().map(((i, g)) => {
      let label-content = text(size: 7pt)[#g.label]
      let content = if i == last { end-nudge(label-content) } else { label-content }
      (i + first-position, content)
    })
    let max-count = calc.max(1, ..groups.map(g => calc.max(g.passed, g.failed)))

    cetz.canvas({
      import cetz.draw: *
      set-style(axes: (shared-zero: false))
      plot.plot(
        size: chart-size,
        x-tick-step: none,
        x-ticks: x-ticks,
        x-min: first-position - 0.5,
        x-max: first-position + n - 1 + 0.5,
        x-label: no-x-label,
        y-label: "Anzahl",
        y-min: 0,
        y-tick-step: nice-y-step(max-count),
        y-decimals: 0,
        axis-style: "school-book",
        {
          plot.add-bar(passed-data, bar-width: 0.4, style: (fill: accent, stroke: none))
          plot.add-bar(failed-data, bar-width: 0.4, style: (fill: color-failed, stroke: none))
        },
      )
    })
    v(2pt)
    text(size: 8pt)[
      #box(width: 8pt, height: 8pt, fill: accent) Bestanden
      #h(10pt)
      #box(width: 8pt, height: 8pt, fill: color-failed) Nicht bestanden
    ]
  }
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
    #kennzahl("Ohne Notenschema:", str(data.counts.awaiting_schema))
    #kennzahl("Bestanden:", str(data.counts.passed))
    #kennzahl("Nicht bestanden:", str(data.counts.failed))
  ],
)
#v(4pt)
#kennzahl("Anwesenheitsquote:", rate-text(data.rates.attendance))
#kennzahl("Bestehensquote:", rate-text(data.rates.passing))
#kennzahl("Durchfallquote:", rate-text(data.rates.failure))

// --- Notenverteilung -----------------------------------------------------------------------------
// The chart sits directly under its own heading, in one non-breakable unit (`block(breakable:
// false)` wrapping both the heading and the `bar-chart` call) — a reader landing on whatever page
// the chart ends up on must always see its caption too, never a bare chart. The table and
// mean/median move *after* the chart rather than before it: with the table first (as in the old
// horizontal-bar layout), the chart was the only thing left to push to a fresh page once the
// table had already used up the remaining space on the previous one, which orphaned it from the
// heading and wasted most of that page. Chart-first packs the flexible, breakable table into
// whatever room is left instead.
#block(breakable: false)[
  = Notenverteilung

  // No `title:` here — the heading above already names this chart; repeating it as the
  // bar-chart's own title would just print the same word twice in a row. Colours: the ten
  // numeric grades in the app accent, "nicht bestanden" in red, "n.e." in grey — built as
  // 3-tuples so `bar-chart` colours each contiguous run on its own.
  #let grade-chart-entries = data.grade_distribution.numeric.map(g => (de(g.grade), g.count, accent))
  #let grade-chart-entries = grade-chart-entries + (
    ("nicht bestanden", data.grade_distribution.failed_count, color-failed),
    ("n.e.", data.grade_distribution.not_attended_count, color-absent),
  )
  #bar-chart(entries: grade-chart-entries)
]

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

// --- Histogramm der Gesamtpunkte -----------------------------------------------------------------
#let total-hist = data.total_points_histogram
#block(breakable: false)[
  = Histogramm der Gesamtpunkte
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
]

// --- Histogramme je Aufgabe ------------------------------------------------------------------
// One `bar-chart` per exercise, each already bundling its own "Aufgabe N" title with its chart
// (see `bar-chart`'s own `breakable: false`). Only the *first* exercise's chart is additionally
// bundled with the section heading itself — a second or third exercise never needs the heading
// repeated, so nothing beyond the first would gain from being tied to it, and tying all of them
// into one giant non-breakable unit would just force a single all-or-nothing block that is far
// more likely to overflow a page than to ever help.
#let exercise-chart(hist) = bar-chart(
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

#block(breakable: false)[
  = Histogramme je Aufgabe
  #if data.exercise_histograms.len() == 0 [
    #emph[Für diese Prüfung sind keine Aufgaben konfiguriert.]
  ] else [
    #exercise-chart(data.exercise_histograms.at(0))
  ]
]
#if data.exercise_histograms.len() > 1 [
  #for hist in data.exercise_histograms.slice(1) [
    #exercise-chart(hist)
  ]
]

// --- Bestehensquote nach Versuch ---------------------------------------------------------------
// Chart directly under the heading (see the Notenverteilung comment above for why), table after.
#block(breakable: false)[
  = Bestehensquote nach Versuch
  #versuch-chart(data.versuch_breakdown)
]

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
