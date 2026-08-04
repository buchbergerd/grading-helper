import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter, Route, Routes } from "react-router";

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
  bonus_points: "0",
  owner_id: 1,
  registration_count: 3,
  recomputation_warning: null,
  share_token: null,
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
  bonus_points: "0.00",
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
    await user.click(screen.getByTestId("attended-2-present"));
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
    await user.click(screen.getByTestId("attended-2-present")); // just to enable Save
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

});

describe("PointsEntryPage — the exam-wide bonus_points field (§7.3)", () => {
  function patchCallsTo(mock: ReturnType<typeof installFetchMock>, path: string) {
    return mock.mock.calls.filter((c) => String(c[0]) === path && c[1]?.method === "PATCH");
  }

  it("shows the grid's bonus_points on load", async () => {
    renderPage();

    const bonusField = (await screen.findByTestId("bonus-points")) as HTMLInputElement;
    expect(bonusField.value).toBe("0.00");
  });

  it("marks the page dirty when bonus_points is edited", async () => {
    const user = userEvent.setup();
    renderPage();

    const bonusField = await screen.findByTestId("bonus-points");
    expect(screen.queryByTestId("unsaved-indicator")).toBeNull();

    await user.clear(bonusField);
    await user.type(bonusField, "3");

    expect(screen.getByTestId("unsaved-indicator")).not.toBeNull();
  });

  it("Speichern with bonus_points changed PATCHes /api/exams/7 with only bonus_points, alongside the points PUT", async () => {
    const user = userEvent.setup();
    const mock = renderPage();

    const bonusField = await screen.findByTestId("bonus-points");
    await user.clear(bonusField);
    await user.type(bonusField, "3");
    await user.click(screen.getByRole("button", { name: "Speichern" }));

    await waitFor(() => {
      expect(patchCallsTo(mock, "/api/exams/7").length).toBe(1);
    });
    const [, init] = patchCallsTo(mock, "/api/exams/7")[0] ?? [];
    const body = JSON.parse(String(init?.body)) as Record<string, unknown>;
    expect(body).toEqual({ bonus_points: "3" });

    expect(
      mock.mock.calls.some((c) => String(c[0]) === "/api/exams/7/points" && c[1]?.method === "PUT"),
    ).toBe(true);
  });

  it("Speichern with bonus_points untouched never PATCHes /api/exams/7", async () => {
    const user = userEvent.setup();
    const mock = renderPage();

    await screen.findByTestId("points-row-1");
    // Touch something unrelated so Speichern is enabled, without touching the bonus field.
    await user.click(screen.getByTestId("attended-2-present"));
    await user.click(screen.getByRole("button", { name: "Speichern" }));

    await waitFor(() => {
      expect(
        mock.mock.calls.some((c) => String(c[0]) === "/api/exams/7/points" && c[1]?.method === "PUT"),
      ).toBe(true);
    });
    expect(patchCallsTo(mock, "/api/exams/7").length).toBe(0);
  });

  it("clearing a non-zero field and saving sends bonus_points \"0\", not an empty string", async () => {
    const user = userEvent.setup();
    const nonZeroGrid: PointsGrid = { ...GRID, bonus_points: "5.00" };
    const mock = renderPage({
      "/api/exams/7/points": (_url, init) =>
        init?.method === "PUT" ? echoPut(init) : jsonResponse(200, nonZeroGrid),
      "/api/exams/7": (_url, init) =>
        init?.method === "PATCH"
          ? jsonResponse(200, { ...EXAM, bonus_points: "0" })
          : jsonResponse(200, EXAM),
    });

    const bonusField = (await screen.findByTestId("bonus-points")) as HTMLInputElement;
    expect(bonusField.value).toBe("5.00");
    await user.clear(bonusField);
    await user.click(screen.getByRole("button", { name: "Speichern" }));

    await waitFor(() => {
      expect(patchCallsTo(mock, "/api/exams/7").length).toBe(1);
    });
    const [, init] = patchCallsTo(mock, "/api/exams/7")[0] ?? [];
    const body = JSON.parse(String(init?.body)) as Record<string, unknown>;
    // Never "" — the decimal-string contract rejects an empty string outright (a 422).
    expect(body).toEqual({ bonus_points: "0" });
  });
});

describe("PointsEntryPage — bonus mode (§7.3, moved here from ExamDetailPage)", () => {
  function patchCallsTo(mock: ReturnType<typeof installFetchMock>, path: string) {
    return mock.mock.calls.filter((c) => String(c[0]) === path && c[1]?.method === "PATCH");
  }

  it("checks the radio matching the grid's bonus_mode on load", async () => {
    renderPage();

    const always = (await screen.findByLabelText("Bonuspunkte zählen immer")) as HTMLInputElement;
    const onlyIfPassing = screen.getByLabelText(
      "Bonuspunkte nur bei Bestehen ohne Bonus",
    ) as HTMLInputElement;
    expect(always.checked).toBe(true);
    expect(onlyIfPassing.checked).toBe(false);
  });

  it("marks the page dirty (shows the unsaved-changes indicator) when the mode is changed", async () => {
    const user = userEvent.setup();
    renderPage();

    await screen.findByTestId("points-row-1");
    expect(screen.queryByTestId("unsaved-indicator")).toBeNull();

    await user.click(screen.getByLabelText("Bonuspunkte nur bei Bestehen ohne Bonus"));

    expect(screen.getByTestId("unsaved-indicator")).not.toBeNull();
  });

  it("Speichern with the mode changed PATCHes /api/exams/7 with only bonus_mode, alongside the points PUT", async () => {
    const user = userEvent.setup();
    const mock = renderPage();

    await screen.findByTestId("points-row-1");
    await user.click(screen.getByLabelText("Bonuspunkte nur bei Bestehen ohne Bonus"));
    await user.click(screen.getByRole("button", { name: "Speichern" }));

    await waitFor(() => {
      expect(patchCallsTo(mock, "/api/exams/7").length).toBe(1);
    });
    const [, init] = patchCallsTo(mock, "/api/exams/7")[0] ?? [];
    const body = JSON.parse(String(init?.body)) as Record<string, unknown>;
    expect(body).toEqual({ bonus_mode: "ONLY_IF_PASSING_WITHOUT_BONUS" });

    // The points PUT still went out in the same save.
    expect(mock.mock.calls.some((c) => String(c[0]) === "/api/exams/7/points" && c[1]?.method === "PUT")).toBe(
      true,
    );
  });

  it("Speichern with the mode untouched never PATCHes /api/exams/7", async () => {
    const user = userEvent.setup();
    const mock = renderPage();

    await screen.findByTestId("points-row-1");
    // Touch something unrelated so Speichern is enabled, without touching the bonus-mode radios.
    await user.click(screen.getByTestId("attended-2-present"));
    await user.click(screen.getByRole("button", { name: "Speichern" }));

    await waitFor(() => {
      expect(mock.mock.calls.some((c) => String(c[0]) === "/api/exams/7/points" && c[1]?.method === "PUT")).toBe(
        true,
      );
    });
    expect(patchCallsTo(mock, "/api/exams/7").length).toBe(0);
  });
});

describe("PointsEntryPage — §8.1 recomputation warning", () => {
  it("shows a warning naming the number of students whose grade moved", async () => {
    const user = userEvent.setup();
    renderPage({
      "/api/exams/7": () =>
        jsonResponse(200, {
          ...EXAM,
          recomputation_warning: { changed: true, affected_registrations: 2, grades_changed: 2 },
        }),
    });

    const bonusField = await screen.findByTestId("bonus-points");
    await user.clear(bonusField);
    await user.type(bonusField, "3");
    await user.click(screen.getByRole("button", { name: "Speichern" }));

    const warning = await screen.findByTestId("recomputation-warning");
    expect(warning.textContent).toContain("2");
    expect(warning.textContent).toContain("Studierende haben");
  });

  it("shows nothing when the save never touched bonus_mode/bonus_points (no exam PATCH at all)", async () => {
    const user = userEvent.setup();
    renderPage();

    await screen.findByTestId("points-row-1");
    await user.click(screen.getByTestId("attended-2-present")); // enable Speichern without touching bonus
    await user.click(screen.getByRole("button", { name: "Speichern" }));

    await waitFor(() => {
      expect(screen.getByText("Die Änderungen wurden gespeichert.")).not.toBeNull();
    });
    expect(screen.queryByTestId("recomputation-warning")).toBeNull();
  });

  it("stays quiet when grades_changed is 0 even though bonus_points was patched", async () => {
    const user = userEvent.setup();
    renderPage({
      "/api/exams/7": () =>
        jsonResponse(200, {
          ...EXAM,
          recomputation_warning: { changed: true, affected_registrations: 2, grades_changed: 0 },
        }),
    });

    const bonusField = await screen.findByTestId("bonus-points");
    await user.clear(bonusField);
    await user.type(bonusField, "3");
    await user.click(screen.getByRole("button", { name: "Speichern" }));

    await waitFor(() => {
      expect(screen.getByText("Die Änderungen wurden gespeichert.")).not.toBeNull();
    });
    expect(screen.queryByTestId("recomputation-warning")).toBeNull();
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
    await user.click(screen.getByTestId("attended-2-present"));
    await user.click(screen.getByRole("button", { name: "Speichern" }));

    await waitFor(() => {
      const body = putBodyOf(mock);
      const claraRow = body.entries.find((row) => row.registration_id === 3);
      expect(claraRow?.points["1"]).toBe("5.00");
      expect(claraRow?.attended).toBe(false);
    });
  });

  it("names each radio's state and student in German via aria-label — the wording moved into the column header, not deleted", async () => {
    renderPage();

    const present = await screen.findByTestId("attended-2-present");
    const absent = screen.getByTestId("attended-2-absent");
    // Same element reached both ways: the accessible name still exists even though no visible
    // "anwesend"/"nicht anwesend" text sits next to the radio any more.
    expect(screen.getByLabelText("Anwesend: Schmidt, Ben")).toBe(present);
    expect(screen.getByLabelText("Nicht anwesend: Schmidt, Ben")).toBe(absent);
  });

  it('renders attended=null as a distinct third state, not as "not attended" — neither radio checked', async () => {
    renderPage();

    const present = (await screen.findByTestId("attended-2-present")) as HTMLInputElement;
    const absent = screen.getByTestId("attended-2-absent") as HTMLInputElement;
    expect(present.checked).toBe(false);
    expect(absent.checked).toBe(false);

    const row = screen.getByTestId("points-row-2");
    expect(row.className).toContain("row-attendance-unknown");
    expect(row.className).not.toContain("row-not-attended");

    // And it must not be disabled the way a not-attended row's inputs are.
    const cell = screen.getByTestId("point-2-1") as HTMLInputElement;
    expect(cell.disabled).toBe(false);
  });

  it("clicking the 'anwesend' radio records attended=true and clicking 'nicht anwesend' records false", async () => {
    const user = userEvent.setup();
    renderPage();

    const presentRadio = (await screen.findByTestId("attended-2-present")) as HTMLInputElement;
    await user.click(presentRadio);
    expect(presentRadio.checked).toBe(true);
    expect(screen.getByTestId("points-row-2").className).not.toContain("row-attendance-unknown");

    const absentRadio = screen.getByTestId("attended-2-absent") as HTMLInputElement;
    await user.click(absentRadio);
    expect(absentRadio.checked).toBe(true);
    expect(presentRadio.checked).toBe(false);
    expect(screen.getByTestId("points-row-2").className).toContain("row-not-attended");
  });

  it("recording one row's attendance leaves every other row's state untouched, including a still-null row", async () => {
    const user = userEvent.setup();
    renderPage();

    await user.click(await screen.findByTestId("attended-2-present"));

    // Anna (row 1) was attended=true from the fixture and must stay that way.
    expect((screen.getByTestId("attended-1-present") as HTMLInputElement).checked).toBe(true);
    // Clara (row 3) was attended=false from the fixture and must stay that way — not flipped to
    // null or true just because a sibling row changed.
    expect((screen.getByTestId("attended-3-absent") as HTMLInputElement).checked).toBe(true);
  });
});

describe("PointsEntryPage — bulk attendance action (§8)", () => {
  it("shows a confirmation dialog naming only the not-yet-recorded rows, and applies to them alone", async () => {
    const user = userEvent.setup();
    const mock = renderPage();

    await screen.findByTestId("points-row-1");
    // Only Ben (row 2) is attended=null in the fixture — Anna is already true, Clara already false.
    const bulkButton = screen.getByTestId("bulk-mark-present");
    await user.click(bulkButton);

    const dialog = await screen.findByRole("alertdialog");
    expect(dialog.textContent).toContain("1");
    expect(dialog.textContent).toContain("nicht");

    await user.click(screen.getByRole("button", { name: "Anwenden" }));

    // Ben is now marked present …
    expect((screen.getByTestId("attended-2-present") as HTMLInputElement).checked).toBe(true);
    // … Clara's explicit "nicht anwesend" must survive untouched …
    expect((screen.getByTestId("attended-3-absent") as HTMLInputElement).checked).toBe(true);
    // … and Anna's pre-existing "anwesend" is unaffected.
    expect((screen.getByTestId("attended-1-present") as HTMLInputElement).checked).toBe(true);

    await user.click(screen.getByRole("button", { name: "Speichern" }));
    await waitFor(() => {
      const body = putBodyOf(mock);
      expect(body.entries.find((row) => row.registration_id === 2)?.attended).toBe(true);
      expect(body.entries.find((row) => row.registration_id === 3)?.attended).toBe(false);
    });
  });

  it("is disabled once no visible row is left unrecorded", async () => {
    const user = userEvent.setup();
    renderPage();

    await screen.findByTestId("points-row-1");
    await user.click(screen.getByTestId("bulk-mark-present"));
    await user.click(await screen.findByRole("button", { name: "Anwenden" }));

    expect((screen.getByTestId("bulk-mark-present") as HTMLButtonElement).disabled).toBe(true);
  });

  it("cancelling the dialog leaves attendance unchanged", async () => {
    const user = userEvent.setup();
    renderPage();

    await screen.findByTestId("points-row-1");
    await user.click(screen.getByTestId("bulk-mark-present"));
    await screen.findByRole("alertdialog");
    await user.click(screen.getByRole("button", { name: "Abbrechen" }));

    expect(screen.queryByRole("alertdialog")).toBeNull();
    const present = screen.getByTestId("attended-2-present") as HTMLInputElement;
    const absent = screen.getByTestId("attended-2-absent") as HTMLInputElement;
    expect(present.checked).toBe(false);
    expect(absent.checked).toBe(false);
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

  it("shows a success notice pointing to the Statistik page once the exam is complete", async () => {
    renderPage();

    await waitFor(() => {
      expect(screen.queryByTestId("completeness-list")).toBeNull();
    });
    expect(screen.getByText(/Alle Daten sind vollständig/)).not.toBeNull();
    expect(screen.getByRole("link", { name: "Statistik-Seite" }).getAttribute("href")).toBe(
      "/klausuren/7/statistik",
    );
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

describe("PointsEntryPage — sort order (§6)", () => {
  /** Deliberately chosen so Matrikelnummer order, Nachname order, and (Studiengang, Nachname)
   * order are all three different permutations of the same three rows — a fixture where they
   * happened to coincide (like the shared `GRID` above) couldn't tell a working re-sort from a
   * no-op. */
  const SORT_GRID: PointsGrid = {
    ...GRID,
    entries: [
      {
        id: 10,
        matrikelnummer: "1010",
        nachname: "Zimmer",
        vorname: "Anna",
        course_code: "A",
        versuch: 1,
        attended: true,
        points: {},
        raw_total: "0.00",
        final_total: null,
        grade: null,
        status: "in_progress",
        is_complete: false,
      },
      {
        id: 20,
        matrikelnummer: "1020",
        nachname: "Adam",
        vorname: "Ben",
        course_code: "A",
        versuch: 1,
        attended: true,
        points: {},
        raw_total: "0.00",
        final_total: null,
        grade: null,
        status: "in_progress",
        is_complete: false,
      },
      {
        id: 30,
        matrikelnummer: "1030",
        nachname: "Mueller",
        vorname: "Clara",
        course_code: "B",
        versuch: 1,
        attended: true,
        points: {},
        raw_total: "0.00",
        final_total: null,
        grade: null,
        status: "in_progress",
        is_complete: false,
      },
    ],
  };

  function visibleRowOrder(): string[] {
    return Array.from(document.querySelectorAll('[data-testid^="points-row-"]')).map(
      (el) => el.getAttribute("data-testid") ?? "",
    );
  }

  function renderSortGrid(): ReturnType<typeof installFetchMock> {
    return renderPage({
      "/api/exams/7/points": (_url, init) =>
        init?.method === "PUT" ? echoPut(init) : jsonResponse(200, SORT_GRID),
    });
  }

  it("defaults to Matrikelnummer order, matching what the server already returns", async () => {
    renderSortGrid();
    await screen.findByTestId("points-row-10");

    expect(visibleRowOrder()).toEqual(["points-row-10", "points-row-20", "points-row-30"]);
  });

  it("re-sorts by Nachname (DIN 5007-1) when chosen", async () => {
    const user = userEvent.setup();
    renderSortGrid();
    await screen.findByTestId("points-row-10");

    await user.selectOptions(screen.getByLabelText("Sortierung"), "nachname");

    expect(visibleRowOrder()).toEqual(["points-row-20", "points-row-30", "points-row-10"]);
  });

  it("re-sorts by Studiengang, then Nachname when chosen", async () => {
    const user = userEvent.setup();
    renderSortGrid();
    await screen.findByTestId("points-row-10");

    await user.selectOptions(screen.getByLabelText("Sortierung"), "course_nachname");

    expect(visibleRowOrder()).toEqual(["points-row-20", "points-row-10", "points-row-30"]);
  });

  it("does not change the save payload, only the on-screen order", async () => {
    const user = userEvent.setup();
    const mock = renderSortGrid();
    await screen.findByTestId("points-row-10");

    await user.selectOptions(screen.getByLabelText("Sortierung"), "nachname");
    // Sorting alone is a display-only change — it must not mark the form dirty on its own, so an
    // actual edit is needed before "Speichern" is even enabled.
    await user.click(screen.getByTestId("attended-20-absent"));
    await user.click(screen.getByRole("button", { name: "Speichern" }));

    await waitFor(() => {
      const body = putBodyOf(mock);
      expect(body.entries.map((row) => row.registration_id).sort((a, b) => a - b)).toEqual([
        10, 20, 30,
      ]);
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

    await user.click(await screen.findByTestId("attended-2-present"));
    await user.click(screen.getByRole("button", { name: "Speichern" }));

    const errorBox = await screen.findByTestId("grid-errors");
    await waitFor(() => {
      expect(errorBox.textContent).toContain(
        "Aufgabe 1: „25“ überschreitet die Höchstpunktzahl 20,00 nicht plausibel.",
      );
    });
  });
});

describe("PointsEntryPage — select-on-focus", () => {
  it("selects a point cell's full contents on focus, so typing replaces it", async () => {
    renderPage();
    const cell = (await screen.findByTestId("point-1-1")) as HTMLInputElement;
    cell.focus();

    expect(cell.selectionStart).toBe(0);
    expect(cell.selectionEnd).toBe(cell.value.length);
  });

  it("selects the bonus_points field's full contents on focus", async () => {
    renderPage();
    const bonusField = (await screen.findByTestId("bonus-points")) as HTMLInputElement;
    bonusField.focus();

    expect(bonusField.selectionStart).toBe(0);
    expect(bonusField.selectionEnd).toBe(bonusField.value.length);
  });

  // A bare `.focus()` (above) can't reproduce the real-browser sequence a mouse click actually
  // fires: mousedown -> focus (select() runs) -> mouseup, where the mouseup is what a browser
  // uses to place the caret / collapse the selection unless it's prevented. This drives a full
  // click through userEvent to pin that the guard (`onSelectableMouseDown`/`onSelectableMouseUp`)
  // is wired up and the selection survives the complete gesture — jsdom's caret/selection
  // simulation is limited, so this is a regression pin more than a full behavioural proof.
  it("keeps the selection intact through a full mouse click, not just a bare .focus()", async () => {
    const user = userEvent.setup();
    renderPage();
    const cell = (await screen.findByTestId("point-1-1")) as HTMLInputElement;
    cell.blur();

    await user.click(cell);

    expect(cell.selectionStart).toBe(0);
    expect(cell.selectionEnd).toBe(cell.value.length);
  });
});
