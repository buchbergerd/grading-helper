import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import type { GradeBarDatum, HistogramBarDatum, VersuchBarDatum } from "./series";

/**
 * Thin Recharts wrappers, one per §9 chart shape. These components only draw — every value they
 * receive was already decided by `series.ts` (pure, unit-tested transforms over the frozen
 * `ExamStatistics` payload from `api/client.ts`). The only number that ever reaches an axis or a
 * bar height here is `count`, an integer headcount that was never a Decimal (see the comment at
 * the top of `series.ts` for why that is fine and nothing else may follow it).
 *
 * `ResponsiveContainer` measures 0×0 in jsdom and renders nothing there — that is expected and is
 * exactly why `ExamStatisticsPage` renders a parallel `<table>` next to every chart for tests
 * (and print/accessibility) to assert against instead.
 *
 * Colours are this app's existing CSS custom properties (`index.css`), passed straight through
 * as `var(--token)` strings in `fill`/`stroke` props — never a new hex value, so the dashboard
 * stays inside the one visual language the rest of the app uses.
 */

const DEFAULT_HEIGHT = 260;

const AXIS_TICK_STYLE = { fontSize: 11, fill: "var(--fg-muted)" };

/**
 * Bars are drawn at their final height immediately, with no grow-in animation.
 *
 * Two reasons, and the second is the one that matters. Presentationally, this dashboard is a live
 * view that re-fetches while grading is in progress — re-animating every bar on each refresh is
 * noise, not feedback. Structurally, an animated `Bar` renders **no** `<rect>` on its first frame
 * and relies on `requestAnimationFrame` to fill in; jsdom never advances that, so every
 * chart-rendering test would see zero bars and quietly assert nothing at all. Turning animation
 * off makes what the tests see identical to what the browser paints.
 */
const ANIMATE = false;
const TOOLTIP_STYLE = {
  fontSize: "0.85rem",
  border: "1px solid var(--border)",
  borderRadius: "var(--radius)",
  background: "var(--bg)",
  color: "var(--fg)",
};

/** A single-series bar chart for one histogram (total points, or one exercise). */
export function HistogramChart({
  data,
  height = DEFAULT_HEIGHT,
}: {
  data: HistogramBarDatum[];
  height?: number;
}): JSX.Element {
  return (
    <ResponsiveContainer width="100%" height={height}>
      <BarChart data={data} margin={{ top: 8, right: 12, left: 0, bottom: 40 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" vertical={false} />
        <XAxis
          dataKey="label"
          tick={AXIS_TICK_STYLE}
          angle={-40}
          textAnchor="end"
          interval={"preserveStartEnd"}
          height={56}
        />
        <YAxis allowDecimals={false} tick={AXIS_TICK_STYLE} width={36} />
        <Tooltip contentStyle={TOOLTIP_STYLE} labelStyle={{ color: "var(--fg)" }} />
        <Bar
          dataKey="count"
          name="Anzahl"
          fill="var(--accent)"
          radius={[3, 3, 0, 0]}
          isAnimationActive={ANIMATE}
        />
      </BarChart>
    </ResponsiveContainer>
  );
}

function colorForGradeKind(kind: GradeBarDatum["kind"]): string {
  switch (kind) {
    case "numeric":
      return "var(--accent)";
    case "failed":
      return "var(--danger)";
    case "not_attended":
      return "var(--fg-muted)";
    default:
      return "var(--accent)";
  }
}

/** The grade-distribution bar chart: numeric grades in the accent colour, "nicht bestanden" in
 * danger red, "n.e." in muted grey — colour is never the only signal (the x-axis labels already
 * name each category), but it lets the eye separate "a real grade" from "not a grade" instantly. */
export function GradeDistributionChart({
  data,
  height = DEFAULT_HEIGHT,
}: {
  data: GradeBarDatum[];
  height?: number;
}): JSX.Element {
  return (
    <ResponsiveContainer width="100%" height={height}>
      <BarChart data={data} margin={{ top: 8, right: 12, left: 0, bottom: 8 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" vertical={false} />
        <XAxis dataKey="label" tick={AXIS_TICK_STYLE} interval={0} />
        <YAxis allowDecimals={false} tick={AXIS_TICK_STYLE} width={36} />
        <Tooltip contentStyle={TOOLTIP_STYLE} labelStyle={{ color: "var(--fg)" }} />
        <Bar dataKey="count" name="Anzahl" radius={[3, 3, 0, 0]} isAnimationActive={ANIMATE}>
          {data.map((entry, index) => (
            <Cell key={`${entry.label}-${index}`} fill={colorForGradeKind(entry.kind)} />
          ))}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
}

/** Pass/fail-by-attempt: grouped bars (passed in ok-green, failed in danger-red) per Versuch. Two
 * series -> a legend is always shown, per this app's accessibility rule for multi-series charts. */
export function VersuchChart({
  data,
  height = DEFAULT_HEIGHT,
}: {
  data: VersuchBarDatum[];
  height?: number;
}): JSX.Element {
  return (
    <ResponsiveContainer width="100%" height={height}>
      <BarChart data={data} margin={{ top: 8, right: 12, left: 0, bottom: 8 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" vertical={false} />
        <XAxis dataKey="label" tick={AXIS_TICK_STYLE} interval={0} />
        <YAxis allowDecimals={false} tick={AXIS_TICK_STYLE} width={36} />
        <Tooltip contentStyle={TOOLTIP_STYLE} labelStyle={{ color: "var(--fg)" }} />
        <Legend wrapperStyle={{ fontSize: "0.8rem", color: "var(--fg-muted)" }} />
        <Bar
          dataKey="passed"
          name="Bestanden"
          fill="var(--ok)"
          radius={[3, 3, 0, 0]}
          isAnimationActive={ANIMATE}
        />
        <Bar
          dataKey="failed"
          name="Nicht bestanden"
          fill="var(--danger)"
          radius={[3, 3, 0, 0]}
          isAnimationActive={ANIMATE}
        />
      </BarChart>
    </ResponsiveContainer>
  );
}
