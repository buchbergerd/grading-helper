/**
 * Pure payload -> chart-series transforms for the §9 dashboard (`ExamStatisticsPage`).
 *
 * Nothing here computes a statistic. `ExamStatistics` (see `api/client.ts`'s "internal report /
 * statistics (§9)" section) already carries every rate, mean, median and bin as a finished,
 * server-rounded value — rates come with `numerator`/`denominator`/`percent`, bin captions
 * arrive as ready-made German strings. This module only reshapes that payload into the flat
 * arrays Recharts wants, plus a couple of small presentation decisions (fixed category order,
 * "which state is this row in") that are worth unit-testing directly rather than burying in JSX.
 *
 * The one number that is allowed to become a JS `number` in this whole module is `count`: every
 * bar's height. `count` is always an **integer** (a headcount) straight off the wire — it never
 * originated as a Decimal, so turning it into a `number` loses nothing §7.0 cares about. A
 * decimal-string field (a percent, a mean, a bin edge) is never parsed to a number here or
 * anywhere downstream of this module: `formatRate`/`formatDecimal` render the string Germany
 * expects, and the raw numerator/denominator ints are shown as ints. Do not "simplify" a bar's
 * `count` into `Number(someDecimalField)` later — that would be reaching for the one thing this
 * comment exists to rule out.
 */

import type {
  ExamStatistics,
  GradeDistribution,
  Histogram,
  Rate,
  StatisticsCounts,
  VersuchGroup,
} from "../api/client";
import { EMPTY_DISPLAY, formatDecimal, formatPercent } from "../util/format";

/** One bar of a histogram chart/table. */
export interface HistogramBarDatum {
  label: string;
  count: number;
}

/** `Histogram.bins` -> bar data, in the order the backend already sorted them (low to high). */
export function histogramSeries(histogram: Histogram): HistogramBarDatum[] {
  return histogram.bins.map((bin) => ({ label: bin.label, count: bin.count }));
}

/**
 * `histogramSeries`, reversed — every points histogram on this page (total, and each exercise)
 * reads "left is good" (user request, 2026-08-03): highest score first, descending to 0, unlike
 * the grade-distribution and Versuch bar charts, which stay in their natural best-to-worst /
 * ascending-attempt-number order.
 */
export function descendingHistogramSeries(histogram: Histogram): HistogramBarDatum[] {
  return histogramSeries(histogram).reverse();
}

/** Which visual treatment a grade-distribution bar gets — a numeric grade uses the accent
 * colour, "nicht bestanden"/"n.e." use the danger/muted tones (never the same as a real grade,
 * so a failing bar can never be mistaken for a low-but-passing one at a glance). */
export type GradeBarKind = "numeric" | "failed" | "not_attended";

export interface GradeBarDatum {
  label: string;
  count: number;
  kind: GradeBarKind;
}

/**
 * `GradeDistribution` -> bars for all ten numeric grades (best to worst, as the backend already
 * ordered `numeric`), plus "nicht bestanden" and "n.e." appended in that fixed order — every
 * exam gets the same twelve categories regardless of whether a given one is currently 0, so a
 * bar chart never silently reshuffles as data comes in.
 */
export function gradeDistributionSeries(distribution: GradeDistribution): GradeBarDatum[] {
  const numericBars: GradeBarDatum[] = distribution.numeric.map((entry) => ({
    // A grade label is a decimal ("1.3" -> "1,3", §14 #6). Routed through `formatDecimal` rather
    // than a local `.replace` so there is exactly one dot-to-comma rule in the frontend — the
    // Typst template has the mirror of it in its own `de()` helper.
    label: formatDecimal(entry.grade),
    count: entry.count,
    kind: "numeric",
  }));
  return [
    ...numericBars,
    { label: "nicht bestanden", count: distribution.failed_count, kind: "failed" },
    { label: "n.e.", count: distribution.not_attended_count, kind: "not_attended" },
  ];
}

/** One attempt-number group's bars plus its already-computed failure rate for the table. */
export interface VersuchBarDatum {
  versuch: number;
  label: string;
  passed: number;
  failed: number;
  failureRate: Rate;
}

/**
 * `ExamStatistics.versuch_breakdown` -> chart/table rows. Passed through as-is, in whatever
 * order (and however sparse — e.g. only attempts 1 and 4 occurring) the backend sent; this
 * function never invents a zero-row for a missing attempt number, since "no row" and "a row of
 * zeros" mean different things (the former: nobody is on their Nth attempt at all).
 */
export function versuchSeries(groups: readonly VersuchGroup[]): VersuchBarDatum[] {
  return groups.map((group) => ({
    versuch: group.versuch,
    label: group.label,
    passed: group.passed,
    failed: group.failed,
    failureRate: group.failure_rate,
  }));
}

/**
 * The one shared rendering of a `Rate`, used everywhere one is shown so the page can't render
 * the same proportion two different ways: `"84,6 % (33 von 39)"`. `percent: null` (a zero
 * denominator) renders as `EMPTY_DISPLAY` rather than a nonsensical "0 von 0".
 */
export function formatRate(rate: Rate): string {
  if (rate.percent === null) return EMPTY_DISPLAY;
  return `${formatPercent(rate.percent)} (${rate.numerator} von ${rate.denominator})`;
}

/**
 * Decides the "grading in progress" banner state (§9's requirement that the dashboard "must not
 * be mistakable for a final result" while points are still being entered). Visible whenever
 * either some non-excluded student isn't yet folded into the grade/total-points charts
 * (`incomplete` or `attendance_not_recorded` students exist), or there is no grading schema
 * configured at all (so no grade could be computed for anybody, `numberGraded` or not).
 */
export interface GradingProgressBanner {
  visible: boolean;
  /** Students not yet reflected in the grade distribution / total-points histogram. */
  pendingCount: number;
  gradingConfigured: boolean;
}

export function gradingProgressBanner(
  counts: StatisticsCounts,
  gradingConfigured: boolean,
): GradingProgressBanner {
  const pendingCount = counts.incomplete + counts.attendance_not_recorded;
  return {
    visible: pendingCount > 0 || !gradingConfigured,
    pendingCount,
    gradingConfigured,
  };
}

/** Convenience bundle of every derived series an `ExamStatistics` payload needs, so the page
 * component computes each one once rather than re-deriving per section. Still pure — just a
 * grouping of the functions above. */
export interface StatisticsSeries {
  gradeDistribution: GradeBarDatum[];
  /** Descending — see `descendingHistogramSeries`. */
  totalPointsHistogram: HistogramBarDatum[];
  /** Which bar of `totalPointsHistogram` to mark as the §9 passing threshold, by its `label` —
   * a Recharts `ReferenceLine`'s `x` wants the category value, not a raw index, and a label
   * lookup is order-independent so this is unaffected by `totalPointsHistogram` being descending.
   * `null` when `passing_threshold_bin_index` is `null` (no grading schema) or the index falls
   * outside the bars this payload actually sent (defensive; not expected). */
  totalPointsThresholdBinLabel: string | null;
  /** Also descending (user request, 2026-08-03: every points histogram on this page reads "left
   * is good", not just the total) — same `descendingHistogramSeries` as `totalPointsHistogram`. */
  exerciseHistograms: { title: string; bars: HistogramBarDatum[] }[];
  versuch: VersuchBarDatum[];
  banner: GradingProgressBanner;
}

/**
 * `passing_threshold_bin_index` -> the label of that bin in `histogram.bins`, or `null`. A thin
 * lookup, not a decimal comparison — the index was already computed exactly in
 * `app/statistics.py` (see its docstring for why that comparison does not belong in a renderer).
 */
export function thresholdBinLabel(histogram: Histogram, binIndex: number | null): string | null {
  if (binIndex === null) return null;
  return histogram.bins[binIndex]?.label ?? null;
}

export function buildStatisticsSeries(stats: ExamStatistics): StatisticsSeries {
  return {
    gradeDistribution: gradeDistributionSeries(stats.grade_distribution),
    totalPointsHistogram: descendingHistogramSeries(stats.total_points_histogram),
    totalPointsThresholdBinLabel: thresholdBinLabel(
      stats.total_points_histogram,
      stats.passing_threshold_bin_index,
    ),
    exerciseHistograms: stats.exercise_histograms.map((histogram) => ({
      title: histogram.title,
      bars: descendingHistogramSeries(histogram),
    })),
    versuch: versuchSeries(stats.versuch_breakdown),
    banner: gradingProgressBanner(stats.counts, stats.grading_configured),
  };
}
