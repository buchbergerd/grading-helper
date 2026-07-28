import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter, Route, Routes } from "react-router-dom";

import ExamDetailPage from "./ExamDetailPage";
import { installFetchMock, jsonResponse } from "../test/mockFetch";
import type { ExamDetail } from "../api/client";

/**
 * The exam the mocked API returns. The point values are chosen so that the test proves two
 * separate things at once:
 *   - "12.50" and "0.75" survive the whole round trip as strings (a JS number would render
 *     "12.5" and would not be able to represent every such value exactly at all);
 *   - the three max points sum to exactly 60, so the threshold preview can be checked against
 *     SPECIFICATION.md 7.5's worked example (95 % -> 57.0, 50 % -> 30.0).
 */
const EXAM: ExamDetail = {
  id: 7,
  lecture_id: 3,
  lecture_name: "Grundlagen der Informationstechnik",
  semester: "WiSe 23/24",
  termin: "1. Termin",
  exam_date: "2024-02-15",
  bonus_mode: "ONLY_IF_PASSING_WITHOUT_BONUS",
  owner_id: 1,
  registration_count: 42,
  exercises: [
    { id: 1, name: "Aufgabe 1", max_points: "12.50", position: 1 },
    { id: 2, name: "Aufgabe 2", max_points: "0.75", position: 2 },
    { id: 3, name: "Aufgabe 3", max_points: "46.75", position: 3 },
  ],
  grading_schema: [
    { grade: "1.0", percentage: "95" },
    { grade: "1.3", percentage: "90" },
    { grade: "1.7", percentage: "85" },
    { grade: "2.0", percentage: "80" },
    { grade: "2.3", percentage: "75" },
    { grade: "2.7", percentage: "70" },
    { grade: "3.0", percentage: "65" },
    { grade: "3.3", percentage: "60" },
    { grade: "3.7", percentage: "55" },
    { grade: "4.0", percentage: "50" },
  ],
};

function renderPage(exam: ExamDetail = EXAM): ReturnType<typeof installFetchMock> {
  const mock = installFetchMock({
    // Both the initial GET and the PATCH answer with the same exam detail.
    "/api/exams/7": () => jsonResponse(200, exam),
  });
  render(
    <MemoryRouter initialEntries={["/klausuren/7"]}>
      <Routes>
        <Route path="/klausuren/:examId" element={<ExamDetailPage />} />
      </Routes>
    </MemoryRouter>,
  );
  return mock;
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("ExamDetailPage — Decimal values stay strings", () => {
  it("puts the server's decimal string into the input field unchanged", async () => {
    renderPage();

    const first = (await screen.findByLabelText(
      "Maximale Punkte der Aufgabe 1",
    )) as HTMLInputElement;
    const second = screen.getByLabelText("Maximale Punkte der Aufgabe 2") as HTMLInputElement;

    // The trailing zero is the whole point: it cannot survive a round trip through a JS
    // number, so seeing it here proves nothing parsed the value into a double.
    expect(first.value).toBe("12.50");
    expect(second.value).toBe("0.75");
  });

  it("uses text inputs, not number inputs, for point values", async () => {
    renderPage();

    const first = (await screen.findByLabelText(
      "Maximale Punkte der Aufgabe 1",
    )) as HTMLInputElement;
    const percentage = screen.getByLabelText("Prozentwert für Note 1,0") as HTMLInputElement;

    // type="number" would expose valueAsNumber and renormalise the displayed text.
    expect(first.type).toBe("text");
    expect(percentage.type).toBe("text");
    expect(percentage.value).toBe("95");
  });

  it("sends credentials with the request and never stores a token itself", async () => {
    const mock = renderPage();

    await screen.findByLabelText("Maximale Punkte der Aufgabe 1");

    const init = mock.mock.calls[0]?.[1];
    expect(init?.credentials).toBe("same-origin");
    expect(window.localStorage.length).toBe(0);
  });
});

describe("ExamDetailPage — grading preview", () => {
  it("shows the total max points in German notation", async () => {
    renderPage();
    const total = await screen.findByTestId("total-max-points");
    expect(total.textContent).toBe("60,00");
  });

  it("shows the 7.2 thresholds for the 7.5 worked example", async () => {
    renderPage();
    await screen.findByTestId("total-max-points");

    expect(screen.getByTestId("threshold-1.0").textContent).toBe("57,00");
    expect(screen.getByTestId("threshold-4.0").textContent).toBe("30,00");
  });

  it("labels the computed thresholds as a preview, not as authoritative", async () => {
    renderPage();
    await screen.findByTestId("total-max-points");
    expect(screen.getByText(/verbindliche Berechnung erfolgt auf dem Server/)).not.toBeNull();
  });

  it("recomputes the threshold from typed German input without a float", async () => {
    const user = userEvent.setup();
    renderPage();

    const percentage = (await screen.findByLabelText(
      "Prozentwert für Note 1,0",
    )) as HTMLInputElement;
    await user.clear(percentage);
    await user.type(percentage, "62,5");

    // 62.5 % of 60 = 37.5 exactly -> stays 37.5 after flooring to the nearest 0.5.
    await waitFor(() => {
      expect(screen.getByTestId("threshold-1.0").textContent).toBe("37,50");
    });
  });

  it("counts a cleared exercise field as 0 in the total instead of hiding it (Fix 1)", async () => {
    const user = userEvent.setup();
    renderPage();

    const first = (await screen.findByLabelText(
      "Maximale Punkte der Aufgabe 1",
    )) as HTMLInputElement;
    await user.clear(first);

    // 12.50 dropped to 0, the other two rows (0.75 + 46.75) unchanged -> 47.50.
    await waitFor(() => {
      expect(screen.getByTestId("total-max-points").textContent).toBe("47,50");
    });
    // The threshold preview keeps updating too — it must never go blank just because a field is
    // mid-edit.
    expect(screen.getByTestId("threshold-1.0").textContent).not.toBe("—");
  });

  it("sums blank and filled exercise fields, blanks counting as 0 (Fix 1)", async () => {
    const user = userEvent.setup();
    renderPage();

    const first = (await screen.findByLabelText(
      "Maximale Punkte der Aufgabe 1",
    )) as HTMLInputElement;
    const second = (await screen.findByLabelText(
      "Maximale Punkte der Aufgabe 2",
    )) as HTMLInputElement;
    const third = (await screen.findByLabelText(
      "Maximale Punkte der Aufgabe 3",
    )) as HTMLInputElement;

    await user.clear(first);
    await user.type(first, "12.5");
    await user.clear(second); // left blank on purpose
    await user.clear(third);
    await user.type(third, "7.25");

    await waitFor(() => {
      expect(screen.getByTestId("total-max-points").textContent).toBe("19,75");
    });
  });

  it("still shows a total of 0 when every exercise field is blank (Fix 1)", async () => {
    const user = userEvent.setup();
    renderPage();

    const first = (await screen.findByLabelText(
      "Maximale Punkte der Aufgabe 1",
    )) as HTMLInputElement;
    const second = (await screen.findByLabelText(
      "Maximale Punkte der Aufgabe 2",
    )) as HTMLInputElement;
    const third = (await screen.findByLabelText(
      "Maximale Punkte der Aufgabe 3",
    )) as HTMLInputElement;
    await user.clear(first);
    await user.clear(second);
    await user.clear(third);

    await waitFor(() => {
      expect(screen.getByTestId("total-max-points").textContent).toBe("0,00");
    });
    // The threshold preview is derived from the total and must stay visible too, not blank.
    expect(screen.getByTestId("threshold-1.0").textContent).toBe("0,00");
  });

  it("still rejects a blank exercise field on save — the 0 shown in the total is display-only", async () => {
    const user = userEvent.setup();
    const mock = renderPage();

    const first = (await screen.findByLabelText(
      "Maximale Punkte der Aufgabe 1",
    )) as HTMLInputElement;
    await user.clear(first);
    await user.click(screen.getByRole("button", { name: "Speichern" }));

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("keine gültige Punktzahl");
    // Only the initial GET happened — nothing was PATCHed, and in particular no "0" was ever
    // submitted in place of the blank field.
    expect(mock.mock.calls.length).toBe(1);
  });
});

describe("ExamDetailPage — Decimal values stay strings on the way out", () => {
  it("PATCHes points and percentages as JSON strings, digits unchanged", async () => {
    const user = userEvent.setup();
    const mock = renderPage();

    await screen.findByLabelText("Maximale Punkte der Aufgabe 1");
    await user.click(screen.getByRole("button", { name: "Speichern" }));

    await waitFor(() => {
      expect(mock.mock.calls.length).toBe(2);
    });

    const init = mock.mock.calls[1]?.[1];
    expect(init?.method).toBe("PATCH");
    const sent = JSON.parse(String(init?.body)) as {
      exercises: { max_points: unknown; position: number }[];
      grading_schema: { percentage: unknown }[];
    };

    // `typeof` is the load-bearing assertion: a JS number here would mean the value passed
    // through a double on its way to the server.
    expect(typeof sent.exercises[0]?.max_points).toBe("string");
    expect(sent.exercises[0]?.max_points).toBe("12.50");
    expect(sent.exercises[1]?.max_points).toBe("0.75");
    expect(typeof sent.grading_schema[0]?.percentage).toBe("string");
    expect(sent.grading_schema[0]?.percentage).toBe("95");
    // Positions are rewritten 1-based and contiguous in editor order.
    expect(sent.exercises.map((exercise) => exercise.position)).toEqual([1, 2, 3]);
  });

  it("canonicalises German comma input to the dot form the API expects", async () => {
    const user = userEvent.setup();
    const mock = renderPage();

    const first = (await screen.findByLabelText(
      "Maximale Punkte der Aufgabe 1",
    )) as HTMLInputElement;
    await user.clear(first);
    await user.type(first, "12,50");
    await user.click(screen.getByRole("button", { name: "Speichern" }));

    await waitFor(() => {
      expect(mock.mock.calls.length).toBe(2);
    });
    const sent = JSON.parse(String(mock.mock.calls[1]?.[1]?.body)) as {
      exercises: { max_points: unknown }[];
    };
    expect(sent.exercises[0]?.max_points).toBe("12.50");
  });
});

describe("ExamDetailPage — an exam without a grading schema yet", () => {
  const FRESH: ExamDetail = { ...EXAM, grading_schema: [] };

  it("can still be saved, and leaves grading_schema untouched", async () => {
    const user = userEvent.setup();
    const mock = renderPage(FRESH);

    const semester = (await screen.findByLabelText("Semester")) as HTMLInputElement;
    await user.clear(semester);
    await user.type(semester, "SoSe 24");
    await user.click(screen.getByRole("button", { name: "Speichern" }));

    await waitFor(() => {
      expect(mock.mock.calls.length).toBe(2);
    });
    const sent = JSON.parse(String(mock.mock.calls[1]?.[1]?.body)) as Record<string, unknown>;
    expect(sent["semester"]).toBe("SoSe 24");
    expect("grading_schema" in sent).toBe(false);
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("still rejects a partially filled schema", async () => {
    const user = userEvent.setup();
    const mock = renderPage(FRESH);

    const percentage = (await screen.findByLabelText(
      "Prozentwert für Note 1,0",
    )) as HTMLInputElement;
    await user.type(percentage, "95");
    await user.click(screen.getByRole("button", { name: "Speichern" }));

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("kein gültiger Prozentwert");
    expect(mock.mock.calls.length).toBe(1);
  });
});

describe("ExamDetailPage — validation", () => {
  it("blocks saving a schema that is not strictly decreasing and shows a German message", async () => {
    const user = userEvent.setup();
    const mock = renderPage();

    const percentage = (await screen.findByLabelText(
      "Prozentwert für Note 4,0",
    )) as HTMLInputElement;
    await user.clear(percentage);
    // 60 % for the 4.0 row is higher than the 3.7 row's 55 % -> no longer strictly decreasing
    await user.type(percentage, "60");

    await user.click(screen.getByRole("button", { name: "Speichern" }));

    // Asserted inside the alert region so the static hint paragraph (which uses the same
    // wording) cannot make this pass by accident.
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("streng fallend");
    expect(alert.textContent).toContain("Note 3,7");
    // Only the initial GET happened — nothing was PATCHed.
    expect(mock.mock.calls.length).toBe(1);
  });
});

describe("ExamDetailPage — Fix 2: per-field error marking", () => {
  it("marks the later (worse) grade of a strictly-decreasing violation, not the earlier one", async () => {
    const user = userEvent.setup();
    renderPage();

    const note27 = (await screen.findByLabelText(
      "Prozentwert für Note 2,7",
    )) as HTMLInputElement;
    const note23 = screen.getByLabelText("Prozentwert für Note 2,3") as HTMLInputElement;

    // 2.3 stays at its original 75 %; raising 2.7 to 80 % makes it no longer strictly lower —
    // the user's own example ("2.7 has a higher percentage than 2.3 -> mark 2.7").
    await user.clear(note27);
    await user.type(note27, "80");

    await waitFor(() => {
      expect(note27.getAttribute("aria-invalid")).toBe("true");
    });
    expect(note23.getAttribute("aria-invalid")).not.toBe("true");
    // A real, associated message next to the field — not colour alone.
    const describedBy = note27.getAttribute("aria-describedby");
    expect(describedBy).not.toBeNull();
    expect(document.getElementById(describedBy ?? "")?.textContent).toContain("2,3");
  });

  it("marks a single out-of-range percentage (0 or 101) as individually invalid", async () => {
    const user = userEvent.setup();
    renderPage();

    const note40 = (await screen.findByLabelText(
      "Prozentwert für Note 4,0",
    )) as HTMLInputElement;
    await user.clear(note40);
    await user.type(note40, "0");

    await waitFor(() => {
      expect(note40.getAttribute("aria-invalid")).toBe("true");
    });

    const note10 = screen.getByLabelText("Prozentwert für Note 1,0") as HTMLInputElement;
    await user.clear(note10);
    await user.type(note10, "101");

    await waitFor(() => {
      expect(note10.getAttribute("aria-invalid")).toBe("true");
    });
  });

  it("marks a non-numeric or <= 0 exercise max-points field, but not an empty one", async () => {
    const user = userEvent.setup();
    renderPage();

    const first = (await screen.findByLabelText(
      "Maximale Punkte der Aufgabe 1",
    )) as HTMLInputElement;
    await user.clear(first);
    await user.type(first, "abc");
    await waitFor(() => {
      expect(first.getAttribute("aria-invalid")).toBe("true");
    });
    // Fix 1 still treats the unparseable field as 0 in the shown total (0.75 + 46.75 = 47.50) —
    // the per-field red marker and the always-on total are independent, both true at once.
    expect(screen.getByTestId("total-max-points").textContent).toBe("47,50");

    await user.clear(first);
    await user.type(first, "0");
    await waitFor(() => {
      expect(first.getAttribute("aria-invalid")).toBe("true");
    });

    // Empty is a normal mid-edit state (Fix 1), not an error — no red marker.
    await user.clear(first);
    expect(first.getAttribute("aria-invalid")).not.toBe("true");
  });

  it("does not flash a field red for an incomplete decimal separator mid-keystroke", async () => {
    const user = userEvent.setup();
    renderPage();

    const first = (await screen.findByLabelText(
      "Maximale Punkte der Aufgabe 1",
    )) as HTMLInputElement;
    await user.clear(first);
    await user.type(first, "12,"); // one keystroke away from "12,5" — not yet a valid decimal
    expect(first.getAttribute("aria-invalid")).not.toBe("true");

    const percentage = (await screen.findByLabelText(
      "Prozentwert für Note 1,0",
    )) as HTMLInputElement;
    await user.clear(percentage);
    await user.type(percentage, "62,");
    expect(percentage.getAttribute("aria-invalid")).not.toBe("true");
  });

  it("marks the field a server 422 names ('Note 2.7') and leaves other fields alone", async () => {
    const user = userEvent.setup();
    const mock = installFetchMock({
      "/api/exams/7": (_url, init) => {
        if (init?.method === "PATCH") {
          return jsonResponse(422, {
            detail: {
              errors: [
                "Prozentwert für Note 2.7 muss größer als 0 und höchstens 100 sein " +
                  "(aktuell: 101,00 %).",
              ],
            },
          });
        }
        return jsonResponse(200, EXAM);
      },
    });
    render(
      <MemoryRouter initialEntries={["/klausuren/7"]}>
        <Routes>
          <Route path="/klausuren/:examId" element={<ExamDetailPage />} />
        </Routes>
      </MemoryRouter>,
    );

    await screen.findByLabelText("Maximale Punkte der Aufgabe 1");
    await user.click(screen.getByRole("button", { name: "Speichern" }));

    await waitFor(() => {
      expect(mock.mock.calls.length).toBe(2);
    });

    const note27 = (await screen.findByLabelText(
      "Prozentwert für Note 2,7",
    )) as HTMLInputElement;
    await waitFor(() => {
      expect(note27.getAttribute("aria-invalid")).toBe("true");
    });
    const describedBy = note27.getAttribute("aria-describedby");
    expect(document.getElementById(describedBy ?? "")?.textContent).toContain("Note 2.7");

    const note23 = screen.getByLabelText("Prozentwert für Note 2,3") as HTMLInputElement;
    expect(note23.getAttribute("aria-invalid")).not.toBe("true");

    // The summary block still shows the server message verbatim — per-field marking is additive.
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("Note 2.7");

    // Editing the field drops the now-stale server verdict rather than leaving it stuck until
    // the next save attempt; the field goes back to whatever the live check says about "70".
    await user.clear(note27);
    await user.type(note27, "70");
    await waitFor(() => {
      expect(note27.getAttribute("aria-invalid")).not.toBe("true");
    });
  });
});
