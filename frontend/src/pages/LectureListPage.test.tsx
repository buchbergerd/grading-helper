import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter, Route, Routes } from "react-router";

import LectureListPage from "./LectureListPage";
import { installFetchMock, jsonResponse } from "../test/mockFetch";
import type { ExamDetail, LectureSummary } from "../api/client";

const LECTURES: LectureSummary[] = [
  { id: 3, name: "Grundlagen der Informationstechnik", created_at: "2024-01-01T00:00:00Z", exam_count: 1 },
];

const IMPORTED_EXAM: ExamDetail = {
  id: 42,
  lecture_id: 9,
  lecture_name: "Signale und Systeme",
  semester: "WiSe 23/24",
  termin: "1. Termin",
  exam_date: "2024-02-15",
  bonus_mode: "ALWAYS",
  bonus_points: "0",
  owner_id: 1,
  registration_count: 2,
  recomputation_warning: null,
  share_token: null,
  exercises: [],
  grading_schema: [],
};

function baseRoutes(
  overrides: Partial<Record<string, (url: string, init: RequestInit | undefined) => Response>> = {},
): Record<string, (url: string, init: RequestInit | undefined) => Response> {
  return {
    "/api/lectures": () => jsonResponse(200, LECTURES),
    ...overrides,
  };
}

function renderPage(
  overrides?: Partial<Record<string, (url: string, init: RequestInit | undefined) => Response>>,
): ReturnType<typeof installFetchMock> {
  const mock = installFetchMock(baseRoutes(overrides));
  render(
    <MemoryRouter initialEntries={["/"]}>
      <Routes>
        <Route path="/" element={<LectureListPage />} />
        <Route path="/klausuren/:examId" element={<p>Klausurseite</p>} />
      </Routes>
    </MemoryRouter>,
  );
  return mock;
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("LectureListPage — whole-exam import", () => {
  it("uploads the selected file as multipart/form-data under the field name 'file'", async () => {
    const user = userEvent.setup();
    const mock = renderPage({
      "/api/exams/import": () =>
        jsonResponse(201, {
          exam: IMPORTED_EXAM,
          lecture_created: false,
          registrations_imported: 2,
        }),
    });

    await screen.findByText("Grundlagen der Informationstechnik");
    const file = new File(['{"format_version":1}'], "export.json", { type: "application/json" });
    await user.upload(screen.getByLabelText("Exportdatei"), file);
    await user.click(screen.getByRole("button", { name: "Importieren" }));

    await waitFor(() => {
      const importCall = mock.mock.calls.find((call) => String(call[0]).includes("/exams/import"));
      expect(importCall).toBeDefined();
      const body = importCall?.[1]?.body;
      expect(body).toBeInstanceOf(FormData);
      const uploaded = (body as FormData).get("file");
      expect((uploaded as File).name).toBe("export.json");

      const headers = importCall?.[1]?.headers;
      const headerKeys =
        headers instanceof Headers
          ? Array.from(headers.keys())
          : Object.keys((headers as Record<string, string> | undefined) ?? {});
      expect(headerKeys.some((key) => key.toLowerCase() === "content-type")).toBe(false);
    });
  });

  it("reuses an existing lecture: shows the reuse notice and a link to the new exam", async () => {
    const user = userEvent.setup();
    renderPage({
      "/api/exams/import": () =>
        jsonResponse(201, {
          exam: IMPORTED_EXAM,
          lecture_created: false,
          registrations_imported: 2,
        }),
    });

    await screen.findByText("Grundlagen der Informationstechnik");
    const file = new File(['{"format_version":1}'], "export.json", { type: "application/json" });
    await user.upload(screen.getByLabelText("Exportdatei"), file);
    await user.click(screen.getByRole("button", { name: "Importieren" }));

    const notice = await screen.findByText(/zugeordnet/);
    expect(notice.textContent).toContain("Signale und Systeme");
    const link = screen.getByRole("link", { name: "Zur importierten Klausur" });
    expect(link.getAttribute("href")).toBe("/klausuren/42");
  });

  it("creating a new lecture: shows the lecture-created notice", async () => {
    const user = userEvent.setup();
    renderPage({
      "/api/exams/import": () =>
        jsonResponse(201, {
          exam: IMPORTED_EXAM,
          lecture_created: true,
          registrations_imported: 0,
        }),
    });

    await screen.findByText("Grundlagen der Informationstechnik");
    const file = new File(['{"format_version":1}'], "export.json", { type: "application/json" });
    await user.upload(screen.getByLabelText("Exportdatei"), file);
    await user.click(screen.getByRole("button", { name: "Importieren" }));

    const notice = await screen.findByText(/wurde dafür angelegt/);
    expect(notice.textContent).toContain("Signale und Systeme");
  });

  it("requires a file to be selected before submitting — no request is sent", async () => {
    const user = userEvent.setup();
    const mock = renderPage();

    await screen.findByText("Grundlagen der Informationstechnik");
    await user.click(screen.getByRole("button", { name: "Importieren" }));

    expect(await screen.findByText("Bitte zuerst eine Exportdatei auswählen.")).not.toBeNull();
    expect(mock.mock.calls.some((call) => String(call[0]).includes("/exams/import"))).toBe(false);
  });

  it("renders every German message from a 422 detail.errors verbatim", async () => {
    const user = userEvent.setup();
    renderPage({
      "/api/exams/import": () =>
        jsonResponse(422, {
          detail: { errors: ["Doppelte Matrikelnummer(n) in der Exportdatei: 1234567."] },
        }),
    });

    await screen.findByText("Grundlagen der Informationstechnik");
    const file = new File(['{"format_version":1}'], "export.json", { type: "application/json" });
    await user.upload(screen.getByLabelText("Exportdatei"), file);
    await user.click(screen.getByRole("button", { name: "Importieren" }));

    expect(
      await screen.findByText("Doppelte Matrikelnummer(n) in der Exportdatei: 1234567."),
    ).not.toBeNull();
  });
});
