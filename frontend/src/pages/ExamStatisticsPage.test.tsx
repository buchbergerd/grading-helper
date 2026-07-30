import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter, Route, Routes } from "react-router-dom";

import ExamStatisticsPage from "./ExamStatisticsPage";
import { blobResponse, installFetchMock, jsonResponse } from "../test/mockFetch";
import type { CompletenessResult, ExamDetail, ExamStatistics } from "../api/client";

/**
 * jsdom has no `ResizeObserver` (it does no layout), which Recharts' `ResponsiveContainer` needs
 * just to mount — without this it throws and the whole tree fails to render, not just the chart.
 * A no-op stand-in is all that's needed since this page's tests assert against the parallel
 * `<table>`s, never against chart SVG (see the note in `statistics/charts.tsx`). Assigned once at
 * module scope, not via `vi.stubGlobal`, so it survives every test's `vi.unstubAllGlobals()`.
 */
if (typeof globalThis.ResizeObserver === "undefined") {
  class SizedResizeObserver {
    private readonly callback: ResizeObserverCallback;

    constructor(callback: ResizeObserverCallback) {
      this.callback = callback;
    }

    observe(target: Element): void {
      // Report a fixed, non-zero box. A no-op observer would technically satisfy Recharts'
      // mount-time requirement, but `ResponsiveContainer` would then resolve to 0x0 and render
      // *nothing* — leaving every chart in this page permanently untested while the suite stayed
      // green. Handing it a real size makes Recharts emit actual SVG, so a wrong `dataKey` or a
      // series bound to a field that does not exist fails a test instead of shipping.
      const entry = {
        target,
        contentRect: { width: 800, height: 400, top: 0, left: 0, bottom: 400, right: 800, x: 0, y: 0 },
      } as unknown as ResizeObserverEntry;
      this.callback([entry], this as unknown as ResizeObserver);
    }
    unobserve(): void {
      // no-op
    }
    disconnect(): void {
      // no-op
    }
  }
  (globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = SizedResizeObserver;
}

const EXAM: ExamDetail = {
  id: 7,
  lecture_id: 3,
  lecture_name: "Grundlagen der Informationstechnik",
  semester: "WiSe 23/24",
  termin: "1. Termin",
  exam_date: "2024-02-15",
  bonus_mode: "ALWAYS",
  owner_id: 1,
  registration_count: 39,
  exercises: [],
  grading_schema: [],
};

/**
 * A fully populated §9 payload: 39 registered, 2 excluded, 35 attended, 2 not attended, 2 still
 * unrecorded, 33 graded (28 passed / 5 failed) and 2 incomplete — every KPI count exercised at
 * once. Two exercises, two Versuch groups (1 and 2, so not every attempt in the schema exists),
 * a grading schema that is fully configured.
 */
const STATS: ExamStatistics = {
  exam_id: 7,
  lecture_name: "Grundlagen der Informationstechnik",
  semester: "WiSe 23/24",
  termin: "1. Termin",
  exam_date: "15.02.2024",
  generated_at: "28.07.2026 10:00",
  max_points: "45.00",
  bonus_mode: "ALWAYS",
  grading_configured: true,
  passing_threshold: "22.50",
  counts: {
    registered: 39,
    excluded: 2,
    attended: 35,
    not_attended: 2,
    attendance_not_recorded: 2,
    graded: 33,
    incomplete: 2,
    awaiting_schema: 0,
    passed: 28,
    failed: 5,
  },
  rates: {
    attendance: { numerator: 35, denominator: 39, percent: "89.7" },
    passing: { numerator: 28, denominator: 33, percent: "84.8" },
    failure: { numerator: 5, denominator: 33, percent: "15.2" },
  },
  grade_distribution: {
    numeric: [
      { grade: "1.0", count: 2 },
      { grade: "1.3", count: 3 },
      { grade: "1.7", count: 4 },
      { grade: "2.0", count: 5 },
      { grade: "2.3", count: 4 },
      { grade: "2.7", count: 3 },
      { grade: "3.0", count: 3 },
      { grade: "3.3", count: 2 },
      { grade: "3.7", count: 1 },
      { grade: "4.0", count: 1 },
    ],
    numeric_count: 28,
    failed_count: 5,
    not_attended_count: 2,
    mean: "2.15",
    median: "2.00",
  },
  total_points_histogram: {
    title: "Gesamtpunkte",
    bin_width: "1.0",
    reference_max: "45.00",
    max_observed: "44.00",
    included_count: 33,
    bins: [
      { lower: "10.0", upper: "11.0", label: "[10;11[", count: 1 },
      { lower: "11.0", upper: "12.0", label: "[11;12]", count: 2 },
    ],
  },
  exercise_histograms: [
    {
      title: "Aufgabe 1",
      bin_width: "0.5",
      reference_max: "20.00",
      max_observed: "19.50",
      included_count: 33,
      bins: [{ lower: "18.0", upper: "18.5", label: "[18;18,5]", count: 3 }],
    },
    {
      title: "Aufgabe 2",
      bin_width: "0.5",
      reference_max: "25.00",
      max_observed: "24.00",
      included_count: 33,
      bins: [{ lower: "23.5", upper: "24.0", label: "[23,5;24]", count: 1 }],
    },
  ],
  versuch_breakdown: [
    {
      versuch: 1,
      label: "1. Versuch",
      registered: 35,
      attended: 32,
      not_attended: 2,
      attendance_not_recorded: 1,
      graded: 30,
      incomplete: 1,
      awaiting_schema: 0,
      passed: 26,
      failed: 4,
      failure_rate: { numerator: 4, denominator: 30, percent: "13.3" },
    },
    {
      versuch: 2,
      label: "2. Versuch",
      registered: 4,
      attended: 3,
      not_attended: 0,
      attendance_not_recorded: 1,
      graded: 3,
      incomplete: 1,
      awaiting_schema: 0,
      passed: 2,
      failed: 1,
      failure_rate: { numerator: 1, denominator: 3, percent: "33.3" },
    },
  ],
};

/** A payload where everything is complete and graded — the in-progress banner must not appear. */
const STATS_COMPLETE: ExamStatistics = {
  ...STATS,
  counts: { ...STATS.counts, incomplete: 0, attendance_not_recorded: 0, attended: 37, graded: 35 },
};

const STATS_NO_SCHEMA: ExamStatistics = {
  ...STATS,
  grading_configured: false,
  passing_threshold: null,
  grade_distribution: {
    ...STATS.grade_distribution,
    numeric: STATS.grade_distribution.numeric.map((entry) => ({ ...entry, count: 0 })),
    numeric_count: 0,
    failed_count: 0,
    mean: null,
    median: null,
  },
};

/**
 * The state an instructor sees before importing anyone: no registrations, no schema, no bins.
 * Every chart must still mount and render an empty plot rather than throwing.
 */
const STATS_EMPTY: ExamStatistics = {
  ...STATS,
  max_points: "0",
  grading_configured: false,
  passing_threshold: null,
  counts: {
    registered: 0,
    excluded: 0,
    attended: 0,
    not_attended: 0,
    attendance_not_recorded: 0,
    graded: 0,
    incomplete: 0,
    awaiting_schema: 0,
    passed: 0,
    failed: 0,
  },
  rates: {
    attendance: { numerator: 0, denominator: 0, percent: null },
    passing: { numerator: 0, denominator: 0, percent: null },
    failure: { numerator: 0, denominator: 0, percent: null },
  },
  grade_distribution: {
    numeric: STATS.grade_distribution.numeric.map((entry) => ({ ...entry, count: 0 })),
    numeric_count: 0,
    failed_count: 0,
    not_attended_count: 0,
    mean: null,
    median: null,
  },
  total_points_histogram: {
    title: "Gesamtpunkte",
    bin_width: "1.0",
    reference_max: "0",
    max_observed: null,
    included_count: 0,
    bins: [],
  },
  exercise_histograms: [],
  versuch_breakdown: [],
};

const COMPLETENESS_OK: CompletenessResult = {
  is_complete: true,
  incomplete_count: 0,
  incomplete_students: [],
};

const COMPLETENESS_INCOMPLETE: CompletenessResult = {
  is_complete: false,
  incomplete_count: 2,
  incomplete_students: [
    {
      id: 2,
      matrikelnummer: "1002",
      nachname: "Schmidt",
      vorname: "Ben",
      attendance_missing: true,
      missing_exercises: [],
    },
    {
      id: 1,
      matrikelnummer: "1001",
      nachname: "Müller",
      vorname: "Anna",
      attendance_missing: false,
      missing_exercises: ["Aufgabe 2"],
    },
  ],
};

function baseRoutes(
  overrides: Partial<Record<string, (url: string, init: RequestInit | undefined) => Response>> = {},
): Record<string, (url: string, init: RequestInit | undefined) => Response> {
  return {
    "/api/exams/7": () => jsonResponse(200, EXAM),
    "/api/exams/7/statistics": () => jsonResponse(200, STATS),
    "/api/exams/7/completeness": () => jsonResponse(200, COMPLETENESS_OK),
    ...overrides,
  };
}

function renderPage(
  overrides?: Partial<Record<string, (url: string, init: RequestInit | undefined) => Response>>,
): ReturnType<typeof installFetchMock> {
  const mock = installFetchMock(baseRoutes(overrides));
  render(
    <MemoryRouter initialEntries={["/klausuren/7/statistik"]}>
      <Routes>
        <Route path="/klausuren/:examId/statistik" element={<ExamStatisticsPage />} />
      </Routes>
    </MemoryRouter>,
  );
  return mock;
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("ExamStatisticsPage — loading", () => {
  it("shows a loading indicator before the data arrives", () => {
    installFetchMock(baseRoutes());
    render(
      <MemoryRouter initialEntries={["/klausuren/7/statistik"]}>
        <Routes>
          <Route path="/klausuren/:examId/statistik" element={<ExamStatisticsPage />} />
        </Routes>
      </MemoryRouter>,
    );
    expect(screen.getByText("Wird geladen …")).not.toBeNull();
  });
});

describe("ExamStatisticsPage — Kennzahlen and rates", () => {
  it("renders every KPI count and the three formatted rates", async () => {
    renderPage();

    await screen.findByText("Interner Bericht — Grundlagen der Informationstechnik");

    expect(screen.getByTestId("kpi-registered").textContent).toContain("39");
    expect(screen.getByTestId("kpi-attended").textContent).toContain("35");
    expect(screen.getByTestId("kpi-not-attended").textContent).toContain("2");
    expect(screen.getByTestId("kpi-attendance-not-recorded").textContent).toContain("2");
    expect(screen.getByTestId("kpi-graded").textContent).toContain("33");
    expect(screen.getByTestId("kpi-incomplete").textContent).toContain("2");

    // formatPercent inserts a non-breaking space before "%" (util/format.ts) —  , not " ".
    expect(screen.getByTestId("rate-attendance").textContent).toContain("89,7 % (35 von 39)");
    expect(screen.getByTestId("rate-passing").textContent).toContain("84,8 % (28 von 33)");
    expect(screen.getByTestId("rate-failure").textContent).toContain("15,2 % (5 von 33)");
  });

  it("shows the mean/median labelled over the numeric_count students, German comma decimals", async () => {
    renderPage();

    const summary = await screen.findByTestId("grade-summary");
    expect(summary.textContent).toContain("2,15");
    expect(summary.textContent).toContain("2,00");
    expect(summary.textContent).toContain("28");
  });

  it("renders the grade distribution, total-points and versuch tables with the expected rows", async () => {
    renderPage();

    await screen.findByTestId("grade-row-1,0");
    expect(screen.getByTestId("grade-row-1,0").textContent).toContain("2");
    expect(screen.getByTestId("grade-row-nicht bestanden").textContent).toContain("5");
    expect(screen.getByTestId("grade-row-n.e.").textContent).toContain("2");

    expect(screen.getByTestId("total-points-histogram-row-0").textContent).toContain("[10;11[");
    expect(screen.getByTestId("total-points-histogram-row-0").textContent).toContain("1");

    expect(screen.getByTestId("exercise-histogram-0-row-0").textContent).toContain("[18;18,5]");
    expect(screen.getByTestId("exercise-histogram-1-row-0").textContent).toContain("[23,5;24]");

    const versuch1 = screen.getByTestId("versuch-row-1");
    expect(versuch1.textContent).toContain("1. Versuch");
    expect(versuch1.textContent).toContain("26");
    expect(versuch1.textContent).toContain("4");
    expect(versuch1.textContent).toContain("13,3 % (4 von 30)");

    const versuch2 = screen.getByTestId("versuch-row-2");
    expect(versuch2.textContent).toContain("2. Versuch");
  });
});

describe("ExamStatisticsPage — collapsed summary tables", () => {
  it("keeps every chart's table collapsed by default, in the DOM but reachable only once opened", async () => {
    renderPage();

    await screen.findByTestId("grade-row-1,0");

    const detailsTestIds = [
      "grade-distribution-table-details",
      "total-points-histogram-table-details",
      "exercise-histogram-0-table-details",
      "exercise-histogram-1-table-details",
      "versuch-table-details",
    ];

    for (const testId of detailsTestIds) {
      const details = screen.getByTestId(testId) as HTMLDetailsElement;
      expect(details.tagName).toBe("DETAILS");
      expect(details.open).toBe(false);
      // The table stays in the DOM even while collapsed (jsdom tests and print both need it).
      expect(details.querySelector("table")).not.toBeNull();
      expect(details.querySelector("summary")?.textContent).toBe("Werte als Tabelle anzeigen");
    }

    // Opening one details element reveals its row content without affecting the others.
    const user = userEvent.setup();
    const totalPointsSummary = screen
      .getByTestId("total-points-histogram-table-details")
      .querySelector("summary")!;
    await user.click(totalPointsSummary);

    const totalPointsDetails = screen.getByTestId(
      "total-points-histogram-table-details",
    ) as HTMLDetailsElement;
    expect(totalPointsDetails.open).toBe(true);
    expect(screen.getByTestId("total-points-histogram-row-0").textContent).toContain("[10;11[");

    const gradeDetails = screen.getByTestId("grade-distribution-table-details") as HTMLDetailsElement;
    expect(gradeDetails.open).toBe(false);
  });
});

describe("ExamStatisticsPage — in-progress banner", () => {
  it("shows the banner when students are incomplete or unrecorded", async () => {
    renderPage();

    const banner = await screen.findByTestId("grading-progress-banner");
    expect(banner.textContent).toContain("noch nicht");
  });

  it("does not show the banner when everything is complete and graded", async () => {
    renderPage({ "/api/exams/7/statistics": () => jsonResponse(200, STATS_COMPLETE) });

    await screen.findByText("Interner Bericht — Grundlagen der Informationstechnik");
    expect(screen.queryByTestId("grading-progress-banner")).toBeNull();
  });

  it("states that no grading schema is configured when grading_configured is false", async () => {
    renderPage({ "/api/exams/7/statistics": () => jsonResponse(200, STATS_NO_SCHEMA) });

    const banner = await screen.findByTestId("grading-progress-banner");
    expect(banner.textContent).toContain("kein vollständiger Notenschlüssel konfiguriert");
  });
});

describe("ExamStatisticsPage — errors", () => {
  it("renders a 403's German message", async () => {
    renderPage({
      "/api/exams/7/statistics": () =>
        jsonResponse(403, { detail: "Keine Berechtigung für diese Aktion." }),
    });

    await waitFor(() => {
      expect(screen.getByText("Keine Berechtigung für diese Aktion.")).not.toBeNull();
    });
  });

  it("renders the generic network-error message when fetch itself fails", async () => {
    renderPage({
      "/api/exams/7/statistics": () => {
        throw new Error("network down");
      },
    });

    await waitFor(() => {
      expect(screen.getByText("Der Server ist nicht erreichbar.")).not.toBeNull();
    });
  });
});

describe("ExamStatisticsPage — PDF download", () => {
  it("fetches the internal-report PDF and triggers a browser download", async () => {
    const user = userEvent.setup();
    const createObjectURL = vi.fn(() => "blob:mock-url");
    const revokeObjectURL = vi.fn();
    vi.stubGlobal("URL", { ...URL, createObjectURL, revokeObjectURL });
    const clickSpy = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});

    const pdfBlob = new Blob(["%PDF-1.4 ..."], { type: "application/pdf" });
    renderPage({
      "/api/exams/7/reports/internal": () =>
        blobResponse(200, pdfBlob, {
          "Content-Disposition":
            "attachment; filename=\"interner-bericht.pdf\"; filename*=UTF-8''interner-bericht.pdf",
        }),
    });

    await user.click(await screen.findByRole("button", { name: "Internen Bericht herunterladen" }));

    await waitFor(() => {
      expect(createObjectURL).toHaveBeenCalledWith(pdfBlob);
    });
    expect(clickSpy).toHaveBeenCalled();
    expect(revokeObjectURL).toHaveBeenCalledWith("blob:mock-url");

    clickSpy.mockRestore();
  });
});

describe("ExamStatisticsPage — completeness gate (§8.1)", () => {
  it("shows a one-line hint with the incomplete count and a link to the Punkte page", async () => {
    renderPage({
      "/api/exams/7/completeness": () => jsonResponse(200, COMPLETENESS_INCOMPLETE),
    });

    const hint = await screen.findByTestId("completeness-incomplete-hint");
    expect(hint.textContent).toContain("2");
    expect(hint.textContent).toContain("Punkte-Seite");
    expect(screen.getByRole("link", { name: "Punkte-Seite" }).getAttribute("href")).toBe(
      "/klausuren/7/punkte",
    );
  });

  it("shows a success notice once the exam is complete", async () => {
    renderPage();

    await waitFor(() => {
      expect(screen.queryByTestId("completeness-incomplete-hint")).toBeNull();
    });
    expect(
      screen.getByText("Alle Daten sind vollständig — Offizielle Berichte können erzeugt werden."),
    ).not.toBeNull();
  });
});

describe("ExamStatisticsPage — §10/§11 report downloads", () => {
  const REPORT_BUTTON_NAMES = [
    "Prüfungsamt-Bericht als PDF herunterladen",
    "Prüfungsamt-Bericht als Excel herunterladen",
    "Notenliste als PDF herunterladen",
    "Notenliste als Excel herunterladen",
  ];

  it("shows all four report buttons, enabled, once complete and the schema is configured", async () => {
    renderPage(); // COMPLETENESS_OK + STATS.grading_configured === true

    for (const name of REPORT_BUTTON_NAMES) {
      const button = (await screen.findByRole("button", { name })) as HTMLButtonElement;
      expect(button.disabled).toBe(false);
    }
    expect(screen.queryByTestId("schema-not-configured-hint")).toBeNull();
  });

  it("shows the buttons greyed out (not hidden) when the exam is complete but the schema isn't configured", async () => {
    renderPage({
      "/api/exams/7/statistics": () => jsonResponse(200, STATS_NO_SCHEMA),
    });

    const hint = await screen.findByTestId("schema-not-configured-hint");
    expect(hint.textContent).toBe("Der Notenschlüssel ist noch nicht vollständig konfiguriert.");
    for (const name of REPORT_BUTTON_NAMES) {
      const button = (await screen.findByRole("button", { name })) as HTMLButtonElement;
      expect(button.disabled).toBe(true);
    }
  });

  it("shows the buttons greyed out (not hidden) and the incomplete hint while the exam is still incomplete", async () => {
    renderPage({
      "/api/exams/7/completeness": () => jsonResponse(200, COMPLETENESS_INCOMPLETE),
    });

    await screen.findByTestId("completeness-incomplete-hint");
    for (const name of REPORT_BUTTON_NAMES) {
      const button = (await screen.findByRole("button", { name })) as HTMLButtonElement;
      expect(button.disabled).toBe(true);
    }
    expect(screen.queryByTestId("schema-not-configured-hint")).toBeNull();
  });

  it("clicking 'Prüfungsamt-Bericht als PDF herunterladen' fetches the right path and triggers a browser download", async () => {
    const user = userEvent.setup();
    const createObjectURL = vi.fn(() => "blob:mock-url");
    const revokeObjectURL = vi.fn();
    vi.stubGlobal("URL", { ...URL, createObjectURL, revokeObjectURL });
    const clickSpy = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});

    const pdfBlob = new Blob(["%PDF-1.4"], { type: "application/pdf" });
    const mock = renderPage({
      "/api/exams/7/reports/examination-office/pdf": () =>
        blobResponse(200, pdfBlob, {
          "Content-Disposition": 'attachment; filename="pruefungsamt.pdf"',
        }),
    });

    await user.click(
      await screen.findByRole("button", { name: "Prüfungsamt-Bericht als PDF herunterladen" }),
    );

    await waitFor(() => {
      expect(createObjectURL).toHaveBeenCalledWith(pdfBlob);
    });
    expect(clickSpy).toHaveBeenCalled();
    expect(revokeObjectURL).toHaveBeenCalledWith("blob:mock-url");
    const call = mock.mock.calls.find((c) =>
      String(c[0]).includes("/exams/7/reports/examination-office/pdf"),
    );
    expect(call).toBeDefined();

    clickSpy.mockRestore();
  });

  it("clicking 'Notenliste als Excel herunterladen' fetches the excel path and shows an error on a stale-UI 409", async () => {
    const user = userEvent.setup();
    renderPage({
      "/api/exams/7/reports/student-results/excel": () =>
        jsonResponse(409, { detail: { errors: ["Der Notenschlüssel ist nicht vollständig konfiguriert."] } }),
    });

    await user.click(
      await screen.findByRole("button", { name: "Notenliste als Excel herunterladen" }),
    );

    const errorBox = await screen.findByTestId("report-download-errors");
    await waitFor(() => {
      expect(errorBox.textContent).toContain(
        "Der Notenschlüssel ist nicht vollständig konfiguriert.",
      );
    });
  });
});

/*
 * Chart rendering. Everything above asserts against the summary tables, which are deliberately
 * independent JSX — so without these, no Recharts component in this app would ever have been
 * executed by a test, and a wrong `dataKey` or a `Bar` bound to a nonexistent field would be
 * invisible to `tsc`, to the suite and to `npm run build` alike. The sized `ResizeObserver` stub
 * at the top of this file is what makes them possible: with a no-op one, `ResponsiveContainer`
 * resolves to 0x0 and Recharts renders nothing at all while the suite stays green.
 */
describe("ExamStatisticsPage — charts actually render", () => {
  async function renderCharts(): Promise<HTMLElement> {
    const { container } = render(
      <MemoryRouter initialEntries={["/klausuren/7/statistik"]}>
        <Routes>
          <Route path="/klausuren/:examId/statistik" element={<ExamStatisticsPage />} />
        </Routes>
      </MemoryRouter>,
    );
    await screen.findByRole("heading", { name: "Kennzahlen" });
    return container;
  }

  it("draws one bar per grade category, per histogram bin and per Versuch series", async () => {
    installFetchMock(baseRoutes());
    const container = await renderCharts();

    // Notenverteilung + Gesamtpunkte + one per exercise + Versuch. Counted on `.recharts-wrapper`
    // (one per chart) rather than `.recharts-surface`, of which the legend renders extra copies.
    expect(container.querySelectorAll(".recharts-wrapper").length).toBe(
      3 + STATS.exercise_histograms.length,
    );

    const expectedBars =
      STATS.grade_distribution.numeric.length +
      2 + // "nicht bestanden" and "n.e."
      STATS.total_points_histogram.bins.length +
      STATS.exercise_histograms.reduce((sum, histogram) => sum + histogram.bins.length, 0) +
      STATS.versuch_breakdown.length * 2; // the passed and failed series
    expect(container.querySelectorAll(".recharts-rectangle").length).toBe(expectedBars);

    // Counting rectangles alone proves too little: Recharts emits a rect for a series bound to a
    // field that does not exist too, just with height 0. Every value in this fixture is non-zero,
    // so every bar must have a positive height — that is what actually pins each `dataKey` to a
    // real field. A typo in one turns its bars flat and fails here.
    const heights = Array.from(container.querySelectorAll(".recharts-rectangle")).map((rect) =>
      Number(rect.getAttribute("height") ?? "0"),
    );
    expect(heights.filter((height) => height > 0).length).toBe(expectedBars);
  });

  it("labels the axes with the payload's own German captions, never canonical decimals", async () => {
    installFetchMock(baseRoutes());
    const container = await renderCharts();

    const tickText = Array.from(container.querySelectorAll(".recharts-cartesian-axis-tick-value"))
      .map((node) => node.textContent)
      .filter((text): text is string => text !== null && text !== "");

    for (const bin of STATS.total_points_histogram.bins) {
      expect(tickText).toContain(bin.label);
    }
    expect(tickText).toContain("1,0");
    expect(tickText).not.toContain("1.0");
  });

  it("renders every chart empty, without bars and without throwing, when there is no data", async () => {
    installFetchMock(
      baseRoutes({
        "/api/exams/7/statistics": () => jsonResponse(200, STATS_EMPTY),
      }),
    );
    const container = await renderCharts();

    expect(container.querySelectorAll(".recharts-rectangle").length).toBe(0);
  });
});
