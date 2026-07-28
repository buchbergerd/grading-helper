import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter, Route, Routes } from "react-router-dom";

import PointsEntryPage from "./PointsEntryPage";
import { installFetchMock, jsonResponse } from "../test/mockFetch";
import type { CompletenessResult, ExamDetail, PointsGrid } from "../api/client";

const EXAM: ExamDetail = {
  id: 7,
  lecture_id: 3,
  lecture_name: "Grundlagen der Informationstechnik",
  semester: "WiSe 23/24",
  termin: "1. Termin",
  exam_date: "2024-02-15",
  bonus_mode: "ALWAYS",
  owner_id: 1,
  registration_count: 3,
  exercises: [],
  grading_schema: [],
};

/**
 * Two exercises, three students covering the three attendance states (§7.4/§8.1):
 *   - Anna (id 1): attended, nothing entered yet -> used for the live-total test.
 *   - Ben (id 2): attended = null ("nicht erfasst"), the third distinct state.
 *   - Clara (id 3): attended = false, but already has a stored exercise-1 value — used to prove
 *     a not-attended row's stored points are never silently erased.
 */
const GRID: PointsGrid = {
  exercises: [
    { id: 1, name: "Aufgabe 1", max_points: "20.00", position: 1 },
    { id: 2, name: "Aufgabe 2", max_points: "5.00", position: 2 },
  ],
  grading_schema: [
    { grade: "1.0", percentage: "95", threshold_points: "23.75" },
    { grade: "4.0", percentage: "50", threshold_points: "12.50" },
  ],
  bonus_mode: "ALWAYS",
  grading_configured: true,
  entries: [
    {
      id: 1,
      matrikelnummer: "1001",
      nachname: "Müller",
      vorname: "Anna",
      course_code: "B.Sc. WiIng ET/IT",
      versuch: 1,
      attended: true,
      bonus_points: "0.00",
      points: { "1": "12.50" },
      raw_total: "12.50",
      final_total: "12.50",
      grade: null,
      status: "in_progress",
      is_complete: false,
    },
    {
      id: 2,
      matrikelnummer: "1002",
      nachname: "Schmidt",
      vorname: "Ben",
      course_code: "B.Sc. WiIng ET/IT",
      versuch: 1,
      attended: null,
      bonus_points: "0.00",
      points: {},
      raw_total: "0.00",
      final_total: null,
      grade: null,
      status: "attendance_missing",
      is_complete: false,
    },
    {
      id: 3,
      matrikelnummer: "1003",
      nachname: "Weber",
      vorname: "Clara",
      course_code: "M.Sc. ET",
      versuch: 2,
      attended: false,
      bonus_points: "0.00",
      points: { "1": "5.00" },
      raw_total: "5.00",
      final_total: null,
      grade: "n.e.",
      status: "not_attended",
      is_complete: true,
    },
  ],
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

/** Generic PUT handler: echoes back a plausible `BulkPointsSaveResult` built from whatever the
 * request body says — `{entries: [...]}` in, `{entries: [...recomputed...], warnings: [...]}`
 * out, matching `backend/app/api/schemas.py` exactly. Good enough for tests that only care about
 * what was *sent*, not about server-side grade math. */
function echoPut(init: RequestInit | undefined): Response {
  const body = JSON.parse(String(init?.body)) as {
    entries: Array<{
      registration_id: number;
      attended: boolean | null;
      bonus_points: string | null;
      points: Record<string, string | null>;
    }>;
  };
  const entries = body.entries.map((row) => {
    const template = GRID.entries.find((entry) => entry.id === row.registration_id);
    const points: Record<string, string> = {};
    for (const [exerciseId, value] of Object.entries(row.points)) {
      if (value !== null) points[exerciseId] = value;
    }
    return {
      id: row.registration_id,
      matrikelnummer: template?.matrikelnummer ?? "",
      nachname: template?.nachname ?? "",
      vorname: template?.vorname ?? "",
      course_code: template?.course_code ?? "",
      versuch: template?.versuch ?? 1,
      attended: row.attended,
      bonus_points: row.bonus_points ?? "0.00",
      points,
      raw_total: "0.00",
      final_total: row.attended === false ? null : "0.00",
      grade: row.attended === false ? "n.e." : null,
      status: "in_progress",
      is_complete: false,
    };
  });
  return jsonResponse(200, { entries, warnings: [] as string[] });
}

function baseRoutes(
  overrides: Partial<Record<string, (url: string, init: RequestInit | undefined) => Response>> = {},
): Record<string, (url: string, init: RequestInit | undefined) => Response> {
  return {
    "/api/exams/7": () => jsonResponse(200, EXAM),
    "/api/exams/7/points": (_url, init) =>
      init?.method === "PUT" ? echoPut(init) : jsonResponse(200, GRID),
    "/api/exams/7/completeness": () => jsonResponse(200, COMPLETENESS_OK),
    ...overrides,
  };
}

function renderPage(
  overrides?: Partial<Record<string, (url: string, init: RequestInit | undefined) => Response>>,
): ReturnType<typeof installFetchMock> {
  const mock = installFetchMock(baseRoutes(overrides));
  render(
    <MemoryRouter initialEntries={["/klausuren/7/punkte"]}>
      <Routes>
        <Route path="/klausuren/:examId/punkte" element={<PointsEntryPage />} />
      </Routes>
    </MemoryRouter>,
  );
  return mock;
}

function putBodyOf(mock: ReturnType<typeof installFetchMock>): {
  entries: Array<{
    registration_id: number;
    attended: boolean | null;
    bonus_points: string | null;
    points: Record<string, string | null>;
  }>;
} {
  const call = mock.mock.calls.find(
    (c) => String(c[0]).includes("/exams/7/points") && c[1]?.method === "PUT",
  );
  if (call === undefined) throw new Error("no PUT /exams/7/points call was made");
  return JSON.parse(String(call[1]?.body)) as ReturnType<typeof putBodyOf>;
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("PointsEntryPage — the decimal rule", () => {
  it('a "12.50" from the API reaches its input unchanged and is sent back as "12.50"', async () => {
    const user = userEvent.setup();
    const mock = renderPage();

    const cell = (await screen.findByTestId("point-1-1")) as HTMLInputElement;
    // Unconverted: the input holds the raw canonical (dot) string exactly as the API sent it —
    // it never round-trips through a JS number, which would have dropped the trailing zero.
    expect(cell.value).toBe("12.50");

    // Trigger a save without touching this cell (toggle a different row's attendance) so the
    // PUT body can be inspected for whether the untouched value survived unchanged.
    await user.selectOptions(screen.getByTestId("attended-2"), "present");
    await user.click(screen.getByRole("button", { name: "Speichern" }));

    await waitFor(() => {
      const body = putBodyOf(mock);
      const annaRow = body.entries.find((row) => row.registration_id === 1);
      expect(annaRow?.points["1"]).toBe("12.50");
    });
  });

  it('accepts German comma input and sends the canonical dot form ("0,75" -> "0.75")', async () => {
    const user = userEvent.setup();
    const mock = renderPage();

    const cell = await screen.findByTestId("point-2-2");
    await user.type(cell, "0,75");
    await user.click(screen.getByRole("button", { name: "Speichern" }));

    await waitFor(() => {
      const body = putBodyOf(mock);
      const benRow = body.entries.find((row) => row.registration_id === 2);
      expect(benRow?.points["2"]).toBe("0.75");
    });
  });
});

describe("PointsEntryPage — empty vs. zero (§8.1)", () => {
  it("sends null for a not-entered cell, distinct from a typed 0", async () => {
    const user = userEvent.setup();
    const mock = renderPage();

    // Anna's Aufgabe 2 is not entered at all (absent from `points` in the fixture).
    await screen.findByTestId("point-1-2");
    await user.selectOptions(screen.getByTestId("attended-2"), "present"); // just to enable Save
    await user.click(screen.getByRole("button", { name: "Speichern" }));

    await waitFor(() => {
      const body = putBodyOf(mock);
      const annaRow = body.entries.find((row) => row.registration_id === 1);
      expect(annaRow?.points["2"]).toBeNull();
    });
  });

  it('sends "0" (not null) for a cell the instructor explicitly typed 0 into', async () => {
    const user = userEvent.setup();
    const mock = renderPage();

    const cell = await screen.findByTestId("point-1-2");
    await user.type(cell, "0");
    await user.click(screen.getByRole("button", { name: "Speichern" }));

    await waitFor(() => {
      const body = putBodyOf(mock);
      const annaRow = body.entries.find((row) => row.registration_id === 1);
      expect(annaRow?.points["2"]).toBe("0");
    });
  });

  it("clearing the bonus field sends null (the server's own default-to-0), never the empty string", async () => {
    const user = userEvent.setup();
    const mock = renderPage();

    // Anna's bonus starts at "0.00" (see the fixture) — clear it entirely.
    const bonusCell = await screen.findByTestId("bonus-1");
    await user.clear(bonusCell);
    await user.click(screen.getByRole("button", { name: "Speichern" }));

    await waitFor(() => {
      const body = putBodyOf(mock);
      const annaRow = body.entries.find((row) => row.registration_id === 1);
      // Never "" — the decimal-string contract rejects an empty string outright (a 422 for the
      // whole batch), so an emptied bonus field must ask for the server's own default instead.
      expect(annaRow?.bonus_points).toBeNull();
    });
  });
});

describe("PointsEntryPage — attendance (§7.4/§8.1)", () => {
  it("disables point entry and shows n.e. for a not-attended row, without erasing its stored points", async () => {
    const user = userEvent.setup();
    const mock = renderPage();

    const claraCell = (await screen.findByTestId("point-3-1")) as HTMLInputElement;
    expect(claraCell.disabled).toBe(true);
    expect(claraCell.value).toBe("5.00"); // the stored value is still shown, just not editable

    expect(screen.getByTestId("grade-3").textContent).toBe("n.e.");

    // Trigger a save via a different row and confirm Clara's stored value survives the payload.
    await user.selectOptions(screen.getByTestId("attended-2"), "present");
    await user.click(screen.getByRole("button", { name: "Speichern" }));

    await waitFor(() => {
      const body = putBodyOf(mock);
      const claraRow = body.entries.find((row) => row.registration_id === 3);
      expect(claraRow?.points["1"]).toBe("5.00");
      expect(claraRow?.attended).toBe(false);
    });
  });

  it('renders attended=null as a distinct third state, not as "not attended"', async () => {
    renderPage();

    const select = (await screen.findByTestId("attended-2")) as HTMLSelectElement;
    expect(select.value).toBe("unknown");

    const row = screen.getByTestId("points-row-2");
    expect(row.className).toContain("row-attendance-unknown");
    expect(row.className).not.toContain("row-not-attended");

    // And it must not be disabled the way a not-attended row's inputs are.
    const cell = screen.getByTestId("point-2-1") as HTMLInputElement;
    expect(cell.disabled).toBe(false);
  });
});

describe("PointsEntryPage — live total and grade preview (§8)", () => {
  it("updates the row total live as cells change, without a JS number", async () => {
    const user = userEvent.setup();
    renderPage();

    await screen.findByTestId("point-1-1");
    // Anna starts with 12.50 already entered in Aufgabe 1 (see the fixture).
    expect(screen.getByTestId("total-1").textContent).toBe("12,50");

    await user.clear(screen.getByTestId("point-1-1"));
    await user.type(screen.getByTestId("point-1-1"), "12");
    await user.type(screen.getByTestId("point-1-2"), "0,75");

    await waitFor(() => {
      expect(screen.getByTestId("total-1").textContent).toBe("12,75");
    });
  });
});

describe("PointsEntryPage — warn, never clamp (§8)", () => {
  it("marks a cell exceeding max_points and shows a warning but keeps the typed value", async () => {
    const user = userEvent.setup();
    renderPage();

    const cell = (await screen.findByTestId("point-2-1")) as HTMLInputElement;
    await user.type(cell, "25"); // Aufgabe 1's max_points is 20.00

    await waitFor(() => {
      expect(cell.className).toContain("cell-warn");
      expect(cell.getAttribute("aria-invalid")).toBe("true");
    });
    // The value is kept exactly as typed — never clamped to the max.
    expect(cell.value).toBe("25");

    const warnings = screen.getByTestId("overflow-warnings");
    expect(warnings.textContent).toContain("überschreitet die Höchstpunktzahl");
  });
});

describe("PointsEntryPage — keyboard entry", () => {
  it("Enter moves focus down the same column", async () => {
    const user = userEvent.setup();
    renderPage();

    const first = await screen.findByTestId("point-1-1");
    first.focus();
    await user.keyboard("{Enter}");

    await waitFor(() => {
      expect(document.activeElement).toBe(screen.getByTestId("point-2-1"));
    });
  });

  it("Tab moves across cells", async () => {
    const user = userEvent.setup();
    renderPage();

    const first = await screen.findByTestId("point-1-1");
    first.focus();
    await user.tab();

    await waitFor(() => {
      expect(document.activeElement).toBe(screen.getByTestId("point-1-2"));
    });
  });
});

describe("PointsEntryPage — completeness gate (§8.1)", () => {
  it("lists the students the API reports as incomplete", async () => {
    renderPage({
      "/api/exams/7/completeness": () => jsonResponse(200, COMPLETENESS_INCOMPLETE),
    });

    const benRow = await screen.findByTestId("incomplete-2");
    expect(benRow.textContent).toContain("Schmidt, Ben");
    expect(benRow.textContent).toContain("Anwesenheit nicht erfasst");

    const annaRow = screen.getByTestId("incomplete-1");
    expect(annaRow.textContent).toContain("Aufgabe 2");
  });

  it("shows a success notice once the exam is complete", async () => {
    renderPage();

    await waitFor(() => {
      expect(screen.queryByTestId("completeness-list")).toBeNull();
    });
    expect(screen.getByText("Alle Daten sind vollständig — Berichte können erzeugt werden.")).not.toBeNull();
  });
});

describe("PointsEntryPage — course filter (§8)", () => {
  it("shows only the selected course's rows", async () => {
    const user = userEvent.setup();
    renderPage();

    await screen.findByTestId("points-row-1");
    expect(screen.getByTestId("points-row-3")).not.toBeNull(); // M.Sc. ET (Clara)

    await user.selectOptions(screen.getByLabelText("Studiengang"), "B.Sc. WiIng ET/IT");

    expect(screen.getByTestId("points-row-1")).not.toBeNull();
    expect(screen.getByTestId("points-row-2")).not.toBeNull();
    expect(screen.queryByTestId("points-row-3")).toBeNull();
  });

  it("keeps edits made under one course filter in the save payload after switching to another", async () => {
    const user = userEvent.setup();
    const mock = renderPage();

    await screen.findByTestId("points-row-1");
    await user.selectOptions(screen.getByLabelText("Studiengang"), "B.Sc. WiIng ET/IT");

    // Edit Anna's (B.Sc.) Aufgabe 2 while only the B.Sc. group is visible.
    await user.type(screen.getByTestId("point-1-2"), "3");

    // Switch to the other course — Anna's row is no longer rendered at all.
    await user.selectOptions(screen.getByLabelText("Studiengang"), "M.Sc. ET");
    expect(screen.queryByTestId("points-row-1")).toBeNull();

    await user.click(screen.getByRole("button", { name: "Speichern" }));

    await waitFor(() => {
      const body = putBodyOf(mock);
      // The save payload is built from every row held in state, not just the ones currently
      // rendered under the filter — Anna's edit must still be there.
      const annaRow = body.entries.find((row) => row.registration_id === 1);
      expect(annaRow?.points["2"]).toBe("3");
    });
  });
});

describe("PointsEntryPage — server errors", () => {
  it("renders a 422's German messages verbatim", async () => {
    const user = userEvent.setup();
    renderPage({
      "/api/exams/7/points": (_url, init) => {
        if (init?.method === "PUT") {
          return jsonResponse(422, {
            detail: {
              errors: ["Aufgabe 1: „25“ überschreitet die Höchstpunktzahl 20,00 nicht plausibel."],
            },
          });
        }
        return jsonResponse(200, GRID);
      },
    });

    await user.selectOptions(await screen.findByTestId("attended-2"), "present");
    await user.click(screen.getByRole("button", { name: "Speichern" }));

    const errorBox = await screen.findByTestId("grid-errors");
    await waitFor(() => {
      expect(errorBox.textContent).toContain(
        "Aufgabe 1: „25“ überschreitet die Höchstpunktzahl 20,00 nicht plausibel.",
      );
    });
  });
});
