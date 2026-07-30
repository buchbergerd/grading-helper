import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";
import { MemoryRouter, Route, Routes } from "react-router-dom";

import LectureDetailPage from "./LectureDetailPage";
import { installFetchMock, jsonResponse } from "../test/mockFetch";
import type { LectureDetail } from "../api/client";

const LECTURE: LectureDetail = {
  id: 3,
  name: "Grundlagen der Informationstechnik",
  created_at: "2024-01-01T00:00:00Z",
  exams: [
    {
      id: 7,
      lecture_id: 3,
      lecture_name: "Grundlagen der Informationstechnik",
      semester: "WiSe 23/24",
      termin: "1. Termin",
      exam_date: "2024-02-15",
      bonus_mode: "ONLY_IF_PASSING_WITHOUT_BONUS",
      owner_id: 1,
    },
  ],
};

function renderPage(
  overrides?: Partial<Record<string, (url: string, init: RequestInit | undefined) => Response>>,
): ReturnType<typeof installFetchMock> {
  const mock = installFetchMock({
    "/api/lectures/3": () => jsonResponse(200, LECTURE),
    ...overrides,
  });
  render(
    <MemoryRouter initialEntries={["/vorlesungen/3"]}>
      <Routes>
        <Route path="/vorlesungen/:lectureId" element={<LectureDetailPage />} />
      </Routes>
    </MemoryRouter>,
  );
  return mock;
}

afterEach(() => {
  cleanup();
});

describe("LectureDetailPage — create-exam form", () => {
  it("does not offer a bonus-mode control in the create form (it belongs on the points-entry page)", async () => {
    renderPage();

    const form = (await screen.findByRole("button", { name: "Klausur anlegen" })).closest("form");
    expect(form).not.toBeNull();
    // The existing exams table legitimately shows "Bonuspunkte" as a column heading — scope the
    // query to the create form so that column doesn't produce a false negative here.
    expect(within(form as HTMLFormElement).queryByText("Bonuspunkte")).toBeNull();
    expect(
      within(form as HTMLFormElement).queryByText("Bonuspunkte zählen immer"),
    ).toBeNull();
    expect(
      within(form as HTMLFormElement).queryByText("Bonuspunkte nur bei Bestehen ohne Bonus"),
    ).toBeNull();
  });

  it("still shows each existing exam's bonus mode in the exam list", async () => {
    renderPage();

    const row = await screen.findByText("WiSe 23/24");
    expect(row.closest("tr")?.textContent).toContain("Bonuspunkte nur bei Bestehen ohne Bonus");
  });

  it("creates an exam without sending bonus_mode at all, letting the server copy it forward", async () => {
    const user = userEvent.setup();
    const mock = renderPage({
      "/api/lectures/3/exams": (_url, init) => {
        if (init?.method === "POST") {
          const sent = JSON.parse(String(init.body)) as Record<string, unknown>;
          expect("bonus_mode" in sent).toBe(false);
          expect(sent["semester"]).toBe("SoSe 24");
          return jsonResponse(201, {
            id: 8,
            lecture_id: 3,
            lecture_name: LECTURE.name,
            semester: "SoSe 24",
            termin: "1. Termin",
            exam_date: null,
            bonus_mode: "ALWAYS",
            owner_id: 1,
            registration_count: 0,
            exercises: [],
            grading_schema: [],
          });
        }
        throw new Error(`unexpected method ${String(init?.method)}`);
      },
    });

    await screen.findByLabelText("Semester");
    await user.type(screen.getByLabelText("Semester"), "SoSe 24");
    await user.click(screen.getByRole("button", { name: "Klausur anlegen" }));

    await waitFor(() => {
      const postCall = mock.mock.calls.find(
        (call) => String(call[0]) === "/api/lectures/3/exams" && call[1]?.method === "POST",
      );
      expect(postCall).toBeDefined();
    });
  });
});
