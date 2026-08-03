import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter, Route, Routes } from "react-router";

import RegistrationsPage from "./RegistrationsPage";
import { blobResponse, installFetchMock, jsonResponse } from "../test/mockFetch";
import type { ExamDetail, RegistrationHeadCount, RegistrationOut } from "../api/client";

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
  exercises: [],
  grading_schema: [],
};

const REG_NORMAL: RegistrationOut = {
  id: 1,
  exam_id: 7,
  matrikelnummer: "1001",
  nachname: "Müller",
  vorname: "Anna",
  course_code: "B.Sc. WiIng ET/IT",
  module_title: "Grundlagen der Informationstechnik (B.Sc. WiIng ET/IT)",
  versuch: 1,
  kommentar: "(angemeldet)",
  flagged: false,
  excluded: false,
  attended: null,
  source_filename: "a.pdf",
};

const REG_FLAGGED: RegistrationOut = {
  ...REG_NORMAL,
  id: 2,
  matrikelnummer: "1002",
  nachname: "Schmidt",
  vorname: "Ben",
  kommentar: "beurlaubt",
  flagged: true,
};

const REG_EXCLUDED: RegistrationOut = {
  ...REG_NORMAL,
  id: 3,
  matrikelnummer: "1003",
  nachname: "Weber",
  vorname: "Clara",
  excluded: true,
};

const REGISTRATIONS: RegistrationOut[] = [REG_NORMAL, REG_FLAGGED, REG_EXCLUDED];

const HEAD_COUNT: RegistrationHeadCount = {
  total: 2,
  per_course: [{ course_code: "B.Sc. WiIng ET/IT", count: 2 }],
};

function baseRoutes(
  overrides: Partial<Record<string, (url: string, init: RequestInit | undefined) => Response>> = {},
): Record<string, (url: string, init: RequestInit | undefined) => Response> {
  return {
    "/api/exams/7": () => jsonResponse(200, EXAM),
    "/api/exams/7/registrations": () => jsonResponse(200, REGISTRATIONS),
    "/api/exams/7/registrations/count": () => jsonResponse(200, HEAD_COUNT),
    ...overrides,
  };
}

function renderPage(
  overrides?: Partial<Record<string, (url: string, init: RequestInit | undefined) => Response>>,
): ReturnType<typeof installFetchMock> {
  const mock = installFetchMock(baseRoutes(overrides));
  render(
    <MemoryRouter initialEntries={["/klausuren/7/anmeldungen"]}>
      <Routes>
        <Route path="/klausuren/:examId/anmeldungen" element={<RegistrationsPage />} />
      </Routes>
    </MemoryRouter>,
  );
  return mock;
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("RegistrationsPage — student list", () => {
  it("highlights flagged rows and shows the flagged reason via Kommentar", async () => {
    renderPage();

    const flaggedRow = await screen.findByTestId("registration-row-2");
    expect(flaggedRow.className).toContain("row-flagged");
    expect(within(flaggedRow).getByTestId("flag-badge-2")).not.toBeNull();
    expect(flaggedRow.textContent).toContain("beurlaubt");
  });

  it("marks excluded rows distinctly and labels them, not merely hiding them", async () => {
    renderPage();

    const excludedRow = await screen.findByTestId("registration-row-3");
    expect(excludedRow.className).toContain("row-excluded");
    expect(within(excludedRow).getByTestId("excluded-badge-3")).not.toBeNull();
  });

  it("hides excluded rows when the toggle is switched off", async () => {
    const user = userEvent.setup();
    renderPage();

    await screen.findByTestId("registration-row-3");
    await user.click(screen.getByLabelText("Ausgeschlossene anzeigen"));

    await waitFor(() => {
      expect(screen.queryByTestId("registration-row-3")).toBeNull();
    });
    // Non-excluded rows stay visible.
    expect(screen.getByTestId("registration-row-1")).not.toBeNull();
  });
});

describe("RegistrationsPage — head count (§6)", () => {
  it("renders the total and the per-course breakdown", async () => {
    renderPage();

    const total = await screen.findByTestId("head-count-total");
    // Exact match, not `toContain`: catches a duplicated-number bug (e.g. rendering the count
    // both via `<strong>` and inside a pluralized label built from the same number).
    expect(total.textContent).toBe("2 angemeldete Studierende");

    const courseRow = screen.getByTestId("head-count-B.Sc. WiIng ET/IT");
    expect(courseRow.textContent).toContain("B.Sc. WiIng ET/IT");
    expect(courseRow.textContent).toContain("2");
  });
});

describe("RegistrationsPage — exclude toggle and delete", () => {
  it("labels each row action by its German name even though it renders as an icon, and the exclude toggle reflects current state", async () => {
    renderPage();

    const row1 = within(await screen.findByTestId("registration-row-1"));
    expect(row1.getByRole("button", { name: "Bearbeiten" })).not.toBeNull();
    expect(row1.getByRole("button", { name: "Ausschließen" })).not.toBeNull();
    expect(row1.getByRole("button", { name: "Löschen" })).not.toBeNull();

    // Row 3 (REG_EXCLUDED) is already excluded, so it must offer the inverse action.
    const row3 = within(screen.getByTestId("registration-row-3"));
    expect(row3.getByRole("button", { name: "Einschließen" })).not.toBeNull();
    expect(row3.queryByRole("button", { name: "Ausschließen" })).toBeNull();
  });

  it("sends PATCH {excluded: true} when excluding a row", async () => {
    const user = userEvent.setup();
    const mock = renderPage({
      "/api/registrations/1": (_url, init) => {
        if (init?.method === "PATCH") return jsonResponse(200, { ...REG_NORMAL, excluded: true });
        throw new Error(`unexpected method ${String(init?.method)}`);
      },
    });

    await screen.findByTestId("registration-row-1");
    const row1 = screen.getByTestId("registration-row-1");
    await user.click(within(row1).getByRole("button", { name: "Ausschließen" }));

    await waitFor(() => {
      const patchCall = mock.mock.calls.find((call) => call[1]?.method === "PATCH");
      expect(patchCall).toBeDefined();
      expect(String(patchCall?.[0])).toContain("/api/registrations/1");
      expect(JSON.parse(String(patchCall?.[1]?.body))).toEqual({ excluded: true });
    });
  });

  it("asks for confirmation, explaining the difference from exclude, before deleting", async () => {
    const user = userEvent.setup();
    const mock = renderPage({
      "/api/registrations/1": (_url, init) => {
        if (init?.method === "DELETE") return jsonResponse(204, undefined);
        throw new Error(`unexpected method ${String(init?.method)}`);
      },
    });

    await screen.findByTestId("registration-row-1");
    const row1 = screen.getByTestId("registration-row-1");
    await user.click(within(row1).getByRole("button", { name: "Löschen" }));

    const dialog = await screen.findByRole("alertdialog");
    expect(dialog.textContent).toContain("Ausschließen");
    expect(dialog.textContent).toContain("unwiderruflich");

    // Cancelling must not send DELETE.
    await user.click(within(dialog).getByRole("button", { name: "Abbrechen" }));
    expect(mock.mock.calls.some((call) => call[1]?.method === "DELETE")).toBe(false);

    // Re-open and confirm.
    await user.click(within(screen.getByTestId("registration-row-1")).getByRole("button", { name: "Löschen" }));
    const dialog2 = await screen.findByRole("alertdialog");
    await user.click(within(dialog2).getByRole("button", { name: "Endgültig löschen" }));

    await waitFor(() => {
      expect(mock.mock.calls.some((call) => call[1]?.method === "DELETE")).toBe(true);
    });
  });
});

describe("RegistrationsPage — remove all (§5.3)", () => {
  it("asks for confirmation before deleting all, and cancelling sends nothing", async () => {
    const user = userEvent.setup();
    const mock = renderPage();

    await screen.findByTestId("registration-row-1");
    await user.click(screen.getByRole("button", { name: "Alle entfernen" }));

    const dialog = await screen.findByRole("alertdialog");
    expect(dialog.textContent).toContain("unwiderruflich");
    expect(dialog.textContent).toContain("Punkte");
    expect(dialog.textContent).toContain("3 Anmeldungen");

    await user.click(within(dialog).getByRole("button", { name: "Abbrechen" }));
    expect(mock.mock.calls.some((call) => call[1]?.method === "DELETE")).toBe(false);
  });

  it("deletes every registration and refreshes the list on confirm", async () => {
    const user = userEvent.setup();
    let deleted = false;
    const mock = renderPage({
      "/api/exams/7/registrations": (_url, init) => {
        if (init?.method === "DELETE") {
          deleted = true;
          return jsonResponse(204, undefined);
        }
        return jsonResponse(200, deleted ? [] : REGISTRATIONS);
      },
    });

    await screen.findByTestId("registration-row-1");
    await user.click(screen.getByRole("button", { name: "Alle entfernen" }));
    const dialog = await screen.findByRole("alertdialog");
    await user.click(within(dialog).getByRole("button", { name: "Alle entfernen" }));

    await waitFor(() => {
      const deleteCall = mock.mock.calls.find(
        (call) =>
          String(call[0]).startsWith("/api/exams/7/registrations") &&
          call[1]?.method === "DELETE",
      );
      expect(deleteCall).toBeDefined();
      expect(String(deleteCall?.[0])).toContain("confirm=true");
    });

    await waitFor(() => {
      expect(screen.getByText("Keine Anmeldungen für diese Auswahl.")).not.toBeNull();
    });
  });

  it("shows an API error when deleting all fails", async () => {
    const user = userEvent.setup();
    renderPage({
      "/api/exams/7/registrations": (_url, init) => {
        if (init?.method === "DELETE") return jsonResponse(409, { detail: "Konflikt." });
        return jsonResponse(200, REGISTRATIONS);
      },
    });

    await screen.findByTestId("registration-row-1");
    await user.click(screen.getByRole("button", { name: "Alle entfernen" }));
    const dialog = await screen.findByRole("alertdialog");
    await user.click(within(dialog).getByRole("button", { name: "Alle entfernen" }));

    const errorBox = await screen.findByTestId("list-errors");
    await waitFor(() => {
      expect(errorBox.textContent).toContain("Konflikt");
    });
  });
});

describe("RegistrationsPage — import", () => {
  it("posts every selected file under the field name 'files' without a Content-Type header", async () => {
    const user = userEvent.setup();
    const mock = renderPage({
      "/api/exams/7/registrations/import": () =>
        jsonResponse(201, {
          imported_total: 2,
          replaced_count: 0,
          files: [
            {
              filename: "a.pdf",
              course_code: "B.Sc. WiIng ET/IT",
              module_title: "Grundlagen der Informationstechnik",
              semester: "WiSe 23/24",
              termin: "1. Termin",
              row_count: 2,
              flagged_count: 0,
              engine: "pdfplumber",
            },
          ],
          warnings: [],
        }),
    });

    await screen.findByLabelText("PDF-Dateien");
    const fileA = new File(["a"], "a.pdf", { type: "application/pdf" });
    const fileB = new File(["b"], "b.pdf", { type: "application/pdf" });
    await user.upload(screen.getByLabelText("PDF-Dateien"), [fileA, fileB]);
    await user.click(screen.getByRole("button", { name: "Importieren" }));

    await waitFor(() => {
      const importCall = mock.mock.calls.find((call) =>
        String(call[0]).includes("/registrations/import"),
      );
      expect(importCall).toBeDefined();
      const init = importCall?.[1];
      const body = init?.body;
      expect(body).toBeInstanceOf(FormData);
      const uploaded = (body as FormData).getAll("files");
      expect(uploaded.length).toBe(2);
      expect((uploaded[0] as File).name).toBe("a.pdf");
      expect((uploaded[1] as File).name).toBe("b.pdf");

      const headers = init?.headers;
      const headerKeys =
        headers instanceof Headers
          ? Array.from(headers.keys())
          : Object.keys((headers as Record<string, string> | undefined) ?? {});
      expect(headerKeys.some((key) => key.toLowerCase() === "content-type")).toBe(false);
    });

    await screen.findByTestId("import-success");
  });

  it("renders every German message from a 422 detail.errors verbatim and prominently", async () => {
    const user = userEvent.setup();
    renderPage({
      "/api/exams/7/registrations/import": () =>
        jsonResponse(422, {
          detail: {
            errors: [
              "„a.pdf“: Die Nr.-Spalte ist nicht vollständig: 3, 4 fehlen.",
              "Es wurde nichts importiert — bitte laden Sie alle Dateien nach der Korrektur erneut hoch.",
            ],
          },
        }),
    });

    await screen.findByLabelText("PDF-Dateien");
    const fileA = new File(["a"], "a.pdf", { type: "application/pdf" });
    await user.upload(screen.getByLabelText("PDF-Dateien"), [fileA]);
    await user.click(screen.getByRole("button", { name: "Importieren" }));

    const errorBox = await screen.findByTestId("import-errors");
    expect(errorBox.textContent).toContain("Es wurde nichts importiert");
    expect(errorBox.textContent).toContain("Die Nr.-Spalte ist nicht vollständig: 3, 4 fehlen.");
    expect(errorBox.textContent).toContain(
      "Es wurde nichts importiert — bitte laden Sie alle Dateien nach der Korrektur erneut hoch.",
    );
  });

  it("surfaces a structured duplicate-Matrikelnummer payload as a table", async () => {
    const user = userEvent.setup();
    renderPage({
      "/api/exams/7/registrations/import": () =>
        jsonResponse(422, {
          detail: {
            errors: ["Die Matrikelnummer 1001 kommt mehrfach vor: ..."],
            duplicates: [
              {
                matrikelnummer: "1001",
                occurrences: [
                  {
                    source: "upload",
                    filename: "a.pdf",
                    course_code: "B.Sc. WiIng ET/IT",
                    module_title: "Grundlagen der Informationstechnik",
                    registration_id: null,
                  },
                  {
                    source: "database",
                    filename: "alt.pdf",
                    course_code: "B.Sc. WiIng ET/IT",
                    module_title: "Grundlagen der Informationstechnik",
                    registration_id: 42,
                  },
                ],
              },
            ],
          },
        }),
    });

    await screen.findByLabelText("PDF-Dateien");
    const fileA = new File(["a"], "a.pdf", { type: "application/pdf" });
    await user.upload(screen.getByLabelText("PDF-Dateien"), [fileA]);
    await user.click(screen.getByRole("button", { name: "Importieren" }));

    const errorBox = await screen.findByTestId("import-errors");
    expect(errorBox.textContent).toContain("1001");
    expect(within(errorBox).getAllByText("B.Sc. WiIng ET/IT").length).toBeGreaterThan(0);
  });
});

describe("RegistrationsPage — attendance list download", () => {
  it("fetches the PDF and triggers a browser download using the server's filename", async () => {
    const user = userEvent.setup();
    const createObjectURL = vi.fn(() => "blob:mock-url");
    const revokeObjectURL = vi.fn();
    vi.stubGlobal("URL", { ...URL, createObjectURL, revokeObjectURL });
    const clickSpy = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});

    const pdfBlob = new Blob(["%PDF-1.4 ..."], { type: "application/pdf" });
    renderPage({
      "/api/exams/7/reports/attendance-list": () =>
        blobResponse(200, pdfBlob, {
          "Content-Disposition":
            "attachment; filename=\"anwesenheitsliste.pdf\"; filename*=UTF-8''anwesenheitsliste.pdf",
        }),
    });

    await user.click(
      await screen.findByRole("button", { name: "Anwesenheitsliste als PDF herunterladen" }),
    );

    await waitFor(() => {
      expect(createObjectURL).toHaveBeenCalledWith(pdfBlob);
    });
    expect(clickSpy).toHaveBeenCalled();
    expect(revokeObjectURL).toHaveBeenCalledWith("blob:mock-url");

    clickSpy.mockRestore();
  });

  it("shows an error message when the download fails", async () => {
    const user = userEvent.setup();
    renderPage({
      "/api/exams/7/reports/attendance-list": () => jsonResponse(404, { detail: "Nicht gefunden." }),
    });

    await user.click(
      await screen.findByRole("button", { name: "Anwesenheitsliste als PDF herunterladen" }),
    );

    const errorBox = await screen.findByTestId("download-errors");
    await waitFor(() => {
      expect(errorBox.textContent).toContain("Nicht gefunden.");
    });
  });

  it("defaults to course-then-Nachname and sends the radio selection as sort_order", async () => {
    const user = userEvent.setup();
    const pdfBlob = new Blob(["%PDF-1.4 ..."], { type: "application/pdf" });
    const mock = renderPage({
      "/api/exams/7/reports/attendance-list": () => blobResponse(200, pdfBlob),
    });

    const defaultRadio = (await screen.findByRole("radio", {
      name: "Studiengang, dann Nachname",
    })) as HTMLInputElement;
    expect(defaultRadio.checked).toBe(true);

    await user.click(screen.getByRole("radio", { name: "Matrikelnummer" }));
    await user.click(
      screen.getByRole("button", { name: "Anwesenheitsliste als PDF herunterladen" }),
    );

    await waitFor(() => {
      const requestedUrl = String(mock.mock.calls.at(-1)?.[0]);
      expect(requestedUrl).toBe("/api/exams/7/reports/attendance-list?sort_order=matrikelnummer");
    });
  });
});

describe("RegistrationsPage — manual add", () => {
  it("adds a late registration via the manual-add form", async () => {
    const user = userEvent.setup();
    const mock = renderPage({
      "/api/exams/7/registrations": (_url, init) => {
        if (init?.method === "POST") {
          const sent = JSON.parse(String(init.body)) as Record<string, unknown>;
          expect(sent["matrikelnummer"]).toBe("2001");
          expect(sent["versuch"]).toBe(1);
          return jsonResponse(201, {
            ...REG_NORMAL,
            id: 99,
            matrikelnummer: "2001",
            nachname: "Neu",
            vorname: "Nina",
          });
        }
        return jsonResponse(200, REGISTRATIONS);
      },
    });

    await screen.findByLabelText("Matr.-Nr.", { selector: "#add-matrikelnummer" });
    await user.type(screen.getByLabelText("Matr.-Nr.", { selector: "#add-matrikelnummer" }), "2001");
    await user.type(screen.getByLabelText("Nachname", { selector: "#add-nachname" }), "Neu");
    await user.type(screen.getByLabelText("Vorname", { selector: "#add-vorname" }), "Nina");
    await user.type(
      screen.getByLabelText("Studiengang", { selector: "#add-course" }),
      "B.Sc. WiIng ET/IT",
    );
    await user.type(
      screen.getByLabelText("Modultitel", { selector: "#add-module" }),
      "Grundlagen der Informationstechnik",
    );
    await user.click(screen.getByRole("button", { name: "Anmeldung hinzufügen" }));

    await waitFor(() => {
      const postCall = mock.mock.calls.find(
        (call) => String(call[0]) === "/api/exams/7/registrations" && call[1]?.method === "POST",
      );
      expect(postCall).toBeDefined();
    });
  });
});
