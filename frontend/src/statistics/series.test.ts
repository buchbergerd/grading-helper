import { describe, expect, it } from "vitest";

import type {
  GradeDistribution,
  Histogram,
  Rate,
  StatisticsCounts,
  VersuchGroup,
} from "../api/client";
import {
  descendingHistogramSeries,
  formatRate,
  gradeDistributionSeries,
  gradingProgressBanner,
  histogramSeries,
  thresholdBinLabel,
  versuchSeries,
} from "./series";

/* ---------------------------------------------------------------------------- histogramSeries */

describe("histogramSeries", () => {
  it("returns an empty array for an empty histogram (nobody has contributed a value yet)", () => {
    const histogram: Histogram = {
      title: "Gesamtpunkte",
      bin_width: "1.0",
      reference_max: "45.00",
      max_observed: null,
      included_count: 0,
      bins: [],
    };
    expect(histogramSeries(histogram)).toEqual([]);
  });

  it("carries the label and count of every bin unchanged, including a bin with count 0", () => {
    const histogram: Histogram = {
      title: "Aufgabe 1",
      bin_width: "0.5",
      reference_max: "20.00",
      max_observed: "18.50",
      included_count: 12,
      bins: [
        { lower: "0.0", upper: "0.5", label: "[0;0,5[", count: 0 },
        { lower: "0.5", upper: "1.0", label: "[0,5;1]", count: 3 },
      ],
    };
    expect(histogramSeries(histogram)).toEqual([
      { label: "[0;0,5[", count: 0 },
      { label: "[0,5;1]", count: 3 },
    ]);
  });

  it("handles a histogram with many bins (~40) without dropping or reordering any of them", () => {
    const bins = Array.from({ length: 40 }, (_, index) => ({
      lower: `${index}.0`,
      upper: `${index + 1}.0`,
      label: index === 39 ? `[${index};${index + 1}]` : `[${index};${index + 1}[`,
      count: index % 3,
    }));
    const histogram: Histogram = {
      title: "Gesamtpunkte",
      bin_width: "1.0",
      reference_max: "40.00",
      max_observed: "39.00",
      included_count: 50,
      bins,
    };
    const series = histogramSeries(histogram);
    expect(series).toHaveLength(40);
    expect(series[0]).toEqual({ label: "[0;1[", count: 0 });
    expect(series[39]).toEqual({ label: "[39;40]", count: 39 % 3 });
  });
});

/* --------------------------------------------------------------- descendingHistogramSeries */

describe("descendingHistogramSeries", () => {
  it("returns histogramSeries reversed — highest bin first", () => {
    const histogram: Histogram = {
      title: "Gesamtpunkte",
      bin_width: "1.0",
      reference_max: "20.00",
      max_observed: "19.00",
      included_count: 6,
      bins: [
        { lower: "10.0", upper: "11.0", label: "[10;11[", count: 1 },
        { lower: "11.0", upper: "12.0", label: "[11;12[", count: 2 },
        { lower: "12.0", upper: "13.0", label: "[12;13]", count: 3 },
      ],
    };
    expect(descendingHistogramSeries(histogram)).toEqual([
      { label: "[12;13]", count: 3 },
      { label: "[11;12[", count: 2 },
      { label: "[10;11[", count: 1 },
    ]);
  });

  it("does not mutate the histogram it was given", () => {
    const histogram: Histogram = {
      title: "Gesamtpunkte",
      bin_width: "1.0",
      reference_max: "10.00",
      max_observed: "9.00",
      included_count: 1,
      bins: [
        { lower: "0.0", upper: "1.0", label: "[0;1[", count: 1 },
        { lower: "1.0", upper: "2.0", label: "[1;2]", count: 0 },
      ],
    };
    descendingHistogramSeries(histogram);
    expect(histogram.bins.map((bin) => bin.label)).toEqual(["[0;1[", "[1;2]"]);
  });

  it("returns an empty array for an empty histogram", () => {
    const histogram: Histogram = {
      title: "Gesamtpunkte",
      bin_width: "1.0",
      reference_max: "0",
      max_observed: null,
      included_count: 0,
      bins: [],
    };
    expect(descendingHistogramSeries(histogram)).toEqual([]);
  });
});

/* ------------------------------------------------------------------------- thresholdBinLabel */

describe("thresholdBinLabel", () => {
  const histogram: Histogram = {
    title: "Gesamtpunkte",
    bin_width: "1.0",
    reference_max: "20.00",
    max_observed: "19.00",
    included_count: 5,
    bins: [
      { lower: "10.0", upper: "11.0", label: "[10;11[", count: 1 },
      { lower: "11.0", upper: "12.0", label: "[11;12]", count: 2 },
    ],
  };

  it("returns null when there is no threshold index (no grading schema configured)", () => {
    expect(thresholdBinLabel(histogram, null)).toBeNull();
  });

  it("returns the label of the bin at the given index", () => {
    expect(thresholdBinLabel(histogram, 0)).toBe("[10;11[");
    expect(thresholdBinLabel(histogram, 1)).toBe("[11;12]");
  });

  it("returns null for an index outside the bins actually sent (defensive, not expected)", () => {
    expect(thresholdBinLabel(histogram, 5)).toBeNull();
  });
});

/* ---------------------------------------------------------------------- gradeDistributionSeries */

describe("gradeDistributionSeries", () => {
  const emptyDistribution: GradeDistribution = {
    numeric: [
      { grade: "1.0", count: 0 },
      { grade: "1.3", count: 0 },
      { grade: "1.7", count: 0 },
      { grade: "2.0", count: 0 },
      { grade: "2.3", count: 0 },
      { grade: "2.7", count: 0 },
      { grade: "3.0", count: 0 },
      { grade: "3.3", count: 0 },
      { grade: "3.7", count: 0 },
      { grade: "4.0", count: 0 },
    ],
    numeric_count: 0,
    failed_count: 0,
    not_attended_count: 0,
    mean: null,
    median: null,
  };

  it("produces all ten numeric grades plus 'nicht bestanden' and 'n.e.', in that fixed order", () => {
    const series = gradeDistributionSeries(emptyDistribution);
    expect(series.map((entry) => entry.label)).toEqual([
      "1,0",
      "1,3",
      "1,7",
      "2,0",
      "2,3",
      "2,7",
      "3,0",
      "3,3",
      "3,7",
      "4,0",
      "nicht bestanden",
      "n.e.",
    ]);
    expect(series.every((entry) => entry.count === 0)).toBe(true);
  });

  it("flags numeric grades, failures and absences with distinct 'kind' values", () => {
    const distribution: GradeDistribution = {
      ...emptyDistribution,
      numeric: emptyDistribution.numeric.map((entry, index) =>
        index === 0 ? { ...entry, count: 5 } : entry,
      ),
      numeric_count: 5,
      failed_count: 2,
      not_attended_count: 1,
      mean: "1.00",
      median: "1.00",
    };
    const series = gradeDistributionSeries(distribution);
    expect(series[0]).toEqual({ label: "1,0", count: 5, kind: "numeric" });
    const failed = series.find((entry) => entry.label === "nicht bestanden");
    expect(failed).toEqual({ label: "nicht bestanden", count: 2, kind: "failed" });
    const notAttended = series.find((entry) => entry.label === "n.e.");
    expect(notAttended).toEqual({ label: "n.e.", count: 1, kind: "not_attended" });
  });

  it("does not choke on null mean/median — those pass through the distribution untouched", () => {
    // gradeDistributionSeries itself doesn't look at mean/median (the page renders them
    // directly), but a null value here must never throw or get silently coerced to 0.
    expect(() => gradeDistributionSeries(emptyDistribution)).not.toThrow();
    expect(emptyDistribution.mean).toBeNull();
    expect(emptyDistribution.median).toBeNull();
  });
});

/* ------------------------------------------------------------------------------- versuchSeries */

describe("versuchSeries", () => {
  it("returns an empty array when there is no versuch breakdown at all", () => {
    expect(versuchSeries([])).toEqual([]);
  });

  it("passes through sparse attempt numbers (e.g. only 1 and 4) without inventing 2/3", () => {
    const rate1: Rate = { numerator: 2, denominator: 10, percent: "20.0" };
    const rate4: Rate = { numerator: 1, denominator: 1, percent: "100.0" };
    const groups: VersuchGroup[] = [
      {
        versuch: 1,
        label: "1. Versuch",
        registered: 10,
        attended: 10,
        not_attended: 0,
        attendance_not_recorded: 0,
        graded: 10,
        incomplete: 0,
        awaiting_schema: 0,
        passed: 8,
        failed: 2,
        failure_rate: rate1,
      },
      {
        versuch: 4,
        label: "4. Versuch",
        registered: 1,
        attended: 1,
        not_attended: 0,
        attendance_not_recorded: 0,
        graded: 1,
        incomplete: 0,
        awaiting_schema: 0,
        passed: 0,
        failed: 1,
        failure_rate: rate4,
      },
    ];
    const series = versuchSeries(groups);
    expect(series).toHaveLength(2);
    expect(series.map((entry) => entry.versuch)).toEqual([1, 4]);
    expect(series[0]).toEqual({
      versuch: 1,
      label: "1. Versuch",
      passed: 8,
      failed: 2,
      failureRate: rate1,
    });
    expect(series[1]?.label).toBe("4. Versuch");
  });
});

/* --------------------------------------------------------------------------------- formatRate */

describe("formatRate", () => {
  it('renders a normal rate as "84,6 % (33 von 39)"', () => {
    const rate: Rate = { numerator: 33, denominator: 39, percent: "84.6" };
    expect(formatRate(rate)).toBe("84,6 % (33 von 39)");
  });

  it("renders percent: null (zero denominator) as the empty-value dash, not '0 von 0'", () => {
    const rate: Rate = { numerator: 0, denominator: 0, percent: null };
    expect(formatRate(rate)).toBe("—");
  });

  it("renders a 0% and a 100% rate without special-casing", () => {
    expect(formatRate({ numerator: 0, denominator: 5, percent: "0.0" })).toBe(
      "0,0 % (0 von 5)",
    );
    expect(formatRate({ numerator: 5, denominator: 5, percent: "100.0" })).toBe(
      "100,0 % (5 von 5)",
    );
  });
});

/* ------------------------------------------------------------------------ gradingProgressBanner */

describe("gradingProgressBanner", () => {
  function counts(overrides: Partial<StatisticsCounts>): StatisticsCounts {
    return {
      registered: 10,
      excluded: 0,
      attended: 10,
      not_attended: 0,
      attendance_not_recorded: 0,
      graded: 10,
      incomplete: 0,
      awaiting_schema: 0,
      passed: 8,
      failed: 2,
      ...overrides,
    };
  }

  it("is not visible when everything is complete and a schema is configured", () => {
    const banner = gradingProgressBanner(counts({}), true);
    expect(banner).toEqual({ visible: false, pendingCount: 0, gradingConfigured: true });
  });

  it("is visible when some students are incomplete", () => {
    const banner = gradingProgressBanner(counts({ incomplete: 3 }), true);
    expect(banner.visible).toBe(true);
    expect(banner.pendingCount).toBe(3);
  });

  it("is visible when some students have no attendance recorded yet", () => {
    const banner = gradingProgressBanner(counts({ attendance_not_recorded: 2 }), true);
    expect(banner.visible).toBe(true);
    expect(banner.pendingCount).toBe(2);
  });

  it("sums incomplete and attendance_not_recorded into pendingCount", () => {
    const banner = gradingProgressBanner(
      counts({ incomplete: 3, attendance_not_recorded: 2 }),
      true,
    );
    expect(banner.pendingCount).toBe(5);
  });

  it("is visible when grading_configured is false, even with pendingCount 0", () => {
    const banner = gradingProgressBanner(counts({}), false);
    expect(banner).toEqual({ visible: true, pendingCount: 0, gradingConfigured: false });
  });
});
