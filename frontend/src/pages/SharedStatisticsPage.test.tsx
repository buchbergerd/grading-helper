import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter, Route, Routes } from "react-router";

import SharedStatisticsPage from "./SharedStatisticsPage";
import { installFetchMock, jsonResponse } from "../test/mockFetch";
import type { ExamStatistics } from "../api/client";

/** Same jsdom-has-no-ResizeObserver shim as `ExamStatisticsPage.test.tsx` — required for
 * `StatisticsDashboard`'s Recharts components to mount at all. */
if (typeof globalThis.ResizeObserver === "undefined") {
  class SizedResizeObserver {
    private readonly callback: ResizeObserverCallback;

    constructor(callback: ResizeObserverCallback) {
      this.callback = callback;
    }

    observe(target: Element): void {
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
  passing_threshold: "11.5",
  passing_threshold_bin_index: 0,
  counts: {
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
  },
  rates: {
    attendance: { numerator: 10, denominator: 10, percent: "100.0" },
    passing: { numerator: 8, denominator: 10, percent: "80.0" },
    failure: { numerator: 2, denominator: 10, percent: "20.0" },
  },
  grade_distribution: {
    numeric: [
      { grade: "1.0", count: 2 },
      { grade: "1.3", count: 1 },
      { grade: "1.7", count: 0 },
      { grade: "2.0", count: 1 },
      { grade: "2.3", count: 1 },
      { grade: "2.7", count: 1 },
      { grade: "3.0", count: 1 },
      { grade: "3.3", count: 0 },
      { grade: "3.7", count: 0 },
      { grade: "4.0", count: 1 },
    ],
    numeric_count: 8,
    failed_count: 2,
    not_attended_count: 0,
    mean: "2.15",
    median: "2.00",
  },
  total_points_histogram: {
    title: "Gesamtpunkte",
    bin_width: "1.0",
    reference_max: "45.00",
    max_observed: "40.00",
    included_count: 10,
    bins: [{ lower: "39.0", upper: "40.0", label: "[39;40]", count: 1 }],
  },
  exercise_histograms: [],
  versuch_breakdown: [
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
      failure_rate: { numerator: 2, denominator: 10, percent: "20.0" },
    },
  ],
};

function renderPage(
  overrides: Partial<Record<string, (url: string, init: RequestInit | undefined) => Response>> = {},
  token = "tok-abc",
): ReturnType<typeof installFetchMock> {
  const mock = installFetchMock({
    "/api/public/statistics/tok-abc": () => jsonResponse(200, STATS),
    ...overrides,
  });
  render(
    <MemoryRouter initialEntries={[`/geteilt/statistik/${token}`]}>
      <Routes>
        <Route path="/geteilt/statistik/:token" element={<SharedStatisticsPage />} />
      </Routes>
    </MemoryRouter>,
  );
  return mock;
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("SharedStatisticsPage", () => {
  it("shows a loading indicator before the data arrives", () => {
    renderPage();
    expect(screen.getByText("Wird geladen …")).not.toBeNull();
  });

  it("renders the dashboard with no session at all — never sends credentials-bearing routes", async () => {
    const mock = renderPage();

    await screen.findByText("Statistik — Grundlagen der Informationstechnik");
    expect(screen.getByTestId("kpi-registered").textContent).toContain("10");
    expect(screen.getByTestId("rate-passing").textContent).toContain("80,0 % (8 von 10)");

    // Only the one public route was ever requested — no exam/completeness/report fetch exists on
    // this page for a session-less caller to accidentally trigger.
    const calledUrls = mock.mock.calls.map((call) => String(call[0]));
    expect(calledUrls.every((url) => url.includes("/api/public/statistics/"))).toBe(true);
  });

  it("never renders a Berichte panel or any report-download button", async () => {
    renderPage();
    await screen.findByText("Statistik — Grundlagen der Informationstechnik");

    expect(screen.queryByRole("heading", { name: "Berichte" })).toBeNull();
    expect(screen.queryByRole("button", { name: /herunterladen/ })).toBeNull();
    expect(screen.queryByTestId("share-link-panel")).toBeNull();
  });

  it("shows the exam date and a read-only-view hint", async () => {
    renderPage();
    await screen.findByText("Statistik — Grundlagen der Informationstechnik");

    expect(screen.getByText(/Klausurdatum 15\.02\.2024/)).not.toBeNull();
    expect(
      screen.getByText("Geteilte, schreibgeschützte Ansicht — Anmeldung nicht erforderlich."),
    ).not.toBeNull();
  });

  it("shows the generic invalid-link message on a 404 and a link back to the login page", async () => {
    renderPage({
      "/api/public/statistics/tok-abc": () =>
        jsonResponse(404, { detail: "Dieser Link ist nicht mehr gültig." }),
    });

    await waitFor(() => {
      expect(screen.getByText("Dieser Link ist nicht mehr gültig.")).not.toBeNull();
    });
    expect(screen.getByRole("link", { name: "Zur Anmeldung" }).getAttribute("href")).toBe("/");
  });

  it("supports the bonus-points simulation, defaulting the field to 0", async () => {
    const user = userEvent.setup();
    const STATS_SIMULATED: ExamStatistics = {
      ...STATS,
      counts: { ...STATS.counts, passed: 9, failed: 1 },
      rates: {
        ...STATS.rates,
        passing: { numerator: 9, denominator: 10, percent: "90.0" },
        failure: { numerator: 1, denominator: 10, percent: "10.0" },
      },
    };
    const mock = renderPage({
      "/api/public/statistics/tok-abc": (url: string) =>
        url.includes("bonus_points_override=")
          ? jsonResponse(200, STATS_SIMULATED)
          : jsonResponse(200, STATS),
    });
    await screen.findByTestId("grade-summary");

    await user.click(screen.getByTestId("simulation-toggle"));
    const input = screen.getByTestId("simulation-bonus-input") as HTMLInputElement;
    // Unlike the authenticated page (which seeds this from the exam's real `bonus_points`), the
    // public payload carries no such value, so the field starts at a plain "0".
    expect(input.value).toBe("0");

    await user.clear(input);
    await user.type(input, "5");

    await waitFor(() => {
      expect(screen.getByTestId("rate-passing").textContent).toContain("90,0 % (9 von 10)");
    });
    const calledUrls = mock.mock.calls.map((call) => String(call[0]));
    expect(
      calledUrls.some((url) => url.includes("/public/statistics/tok-abc?bonus_points_override=5")),
    ).toBe(true);
  });
});
