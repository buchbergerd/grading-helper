import { afterEach, describe, expect, it, vi } from "vitest";

import { blobResponse, installFetchMock, jsonResponse } from "../test/mockFetch";
import {
  downloadAttendanceList,
  extractDuplicates,
  importRegistrations,
  listRegistrations,
  type RegistrationOut,
} from "./client";
import { ApiError } from "./client";

afterEach(() => {
  vi.unstubAllGlobals();
});

const REGISTRATION: RegistrationOut = {
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

describe("importRegistrations", () => {
  it("builds a multipart FormData with every file under 'files' and no manual Content-Type", async () => {
    const mock = installFetchMock({
      "/api/exams/7/registrations/import": () =>
        jsonResponse(201, {
          imported_total: 2,
          replaced_count: 0,
          files: [],
          warnings: [],
        }),
    });

    const fileA = new File(["a"], "a.pdf", { type: "application/pdf" });
    const fileB = new File(["b"], "b.pdf", { type: "application/pdf" });
    await importRegistrations(7, [fileA, fileB], false);

    expect(mock.mock.calls.length).toBe(1);
    const init = mock.mock.calls[0]?.[1];
    expect(init?.method).toBe("POST");
    expect(init?.body).toBeInstanceOf(FormData);
    const form = init?.body as FormData;
    expect(form.getAll("files")).toEqual([fileA, fileB]);
    expect(form.get("replace_existing")).toBe("false");

    const headers = init?.headers as Record<string, string> | Headers | undefined;
    const keys =
      headers instanceof Headers ? Array.from(headers.keys()) : Object.keys(headers ?? {});
    expect(keys.some((key) => key.toLowerCase() === "content-type")).toBe(false);
  });

  it("sends replace_existing=true as a form field, as a string", async () => {
    const mock = installFetchMock({
      "/api/exams/7/registrations/import": () =>
        jsonResponse(201, { imported_total: 0, replaced_count: 0, files: [], warnings: [] }),
    });
    await importRegistrations(7, [new File(["a"], "a.pdf")], true);
    const form = mock.mock.calls[0]?.[1]?.body as FormData;
    expect(form.get("replace_existing")).toBe("true");
  });

  it("throws an ApiError with every German message on a 422 and exposes the raw duplicates", async () => {
    installFetchMock({
      "/api/exams/7/registrations/import": () =>
        jsonResponse(422, {
          detail: {
            errors: ["Fehler A", "Fehler B"],
            duplicates: [
              {
                matrikelnummer: "1001",
                occurrences: [
                  {
                    source: "upload",
                    filename: "a.pdf",
                    course_code: "X",
                    module_title: "Y",
                    registration_id: null,
                  },
                ],
              },
            ],
          },
        }),
    });

    try {
      await importRegistrations(7, [new File(["a"], "a.pdf")], false);
      throw new Error("expected importRegistrations to throw");
    } catch (error) {
      expect(error).toBeInstanceOf(ApiError);
      expect((error as ApiError).messages).toEqual(["Fehler A", "Fehler B"]);
      const duplicates = extractDuplicates(error);
      expect(duplicates).not.toBeNull();
      expect(duplicates?.[0]?.matrikelnummer).toBe("1001");
      expect(duplicates?.[0]?.occurrences[0]?.course_code).toBe("X");
    }
  });
});

describe("listRegistrations", () => {
  it("passes course_code as a query parameter when given", async () => {
    const mock = installFetchMock({
      "/api/exams/7/registrations": () => jsonResponse(200, [REGISTRATION]),
    });
    await listRegistrations(7, { courseCode: "B.Sc. WiIng ET/IT" });
    const url = String(mock.mock.calls[0]?.[0]);
    expect(url).toContain("course_code=B.Sc.%20WiIng%20ET%2FIT");
  });
});

describe("downloadAttendanceList", () => {
  it("returns the blob and the RFC 5987 UTF-8 filename from Content-Disposition", async () => {
    const pdfBlob = new Blob(["%PDF-1.4"], { type: "application/pdf" });
    installFetchMock({
      "/api/exams/7/reports/attendance-list": () =>
        blobResponse(200, pdfBlob, {
          "Content-Disposition":
            "attachment; filename=\"anwesenheitsliste_Oeztuerk.pdf\"; filename*=UTF-8''anwesenheitsliste_%C3%96ztürk.pdf",
        }),
    });

    const result = await downloadAttendanceList(7, "course_nachname");
    expect(result.blob).toBe(pdfBlob);
    expect(result.filename).toBe("anwesenheitsliste_Öztürk.pdf");
  });

  it("falls back to a default filename when the header is absent", async () => {
    const pdfBlob = new Blob(["%PDF-1.4"], { type: "application/pdf" });
    installFetchMock({
      "/api/exams/7/reports/attendance-list": () => blobResponse(200, pdfBlob),
    });

    const result = await downloadAttendanceList(7, "course_nachname");
    expect(result.filename).toBe("anwesenheitsliste.pdf");
  });

  it("throws an ApiError on a non-2xx response instead of returning a blob", async () => {
    installFetchMock({
      "/api/exams/7/reports/attendance-list": () => jsonResponse(404, { detail: "Nicht gefunden." }),
    });

    await expect(downloadAttendanceList(7, "course_nachname")).rejects.toBeInstanceOf(ApiError);
  });

  it("sends the chosen sort order as a query parameter", async () => {
    const pdfBlob = new Blob(["%PDF-1.4"], { type: "application/pdf" });
    const mock = installFetchMock({
      "/api/exams/7/reports/attendance-list": () => blobResponse(200, pdfBlob),
    });

    await downloadAttendanceList(7, "matrikelnummer");

    const requestedUrl = String(mock.mock.calls[0]?.[0]);
    expect(requestedUrl).toBe("/api/exams/7/reports/attendance-list?sort_order=matrikelnummer");
  });
});
