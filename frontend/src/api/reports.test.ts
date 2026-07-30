import { afterEach, describe, expect, it, vi } from "vitest";

import { blobResponse, installFetchMock, jsonResponse } from "../test/mockFetch";
import {
  ApiError,
  downloadExaminationOfficeExcel,
  downloadExaminationOfficePdf,
  downloadStudentResultsExcel,
  downloadStudentResultsPdf,
} from "./client";

afterEach(() => {
  vi.unstubAllGlobals();
});

const EXCEL_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet";

describe("downloadExaminationOfficePdf", () => {
  it("GETs the examination-office PDF path and returns the blob plus filename", async () => {
    const pdfBlob = new Blob(["%PDF-1.4"], { type: "application/pdf" });
    const mock = installFetchMock({
      "/api/exams/7/reports/examination-office/pdf": () =>
        blobResponse(200, pdfBlob, {
          "Content-Disposition": 'attachment; filename="pruefungsamt.pdf"',
        }),
    });

    const result = await downloadExaminationOfficePdf(7);
    expect(result.blob).toBe(pdfBlob);
    expect(result.filename).toBe("pruefungsamt.pdf");
    expect(String(mock.mock.calls[0]?.[0])).toContain(
      "/api/exams/7/reports/examination-office/pdf",
    );
    const init = mock.mock.calls[0]?.[1];
    const headers = init?.headers as Record<string, string>;
    expect(headers.Accept).toBe("application/pdf");
  });

  it("falls back to pruefungsamt.pdf when Content-Disposition is absent", async () => {
    const pdfBlob = new Blob(["%PDF-1.4"], { type: "application/pdf" });
    installFetchMock({
      "/api/exams/7/reports/examination-office/pdf": () => blobResponse(200, pdfBlob),
    });

    const result = await downloadExaminationOfficePdf(7);
    expect(result.filename).toBe("pruefungsamt.pdf");
  });

  it("surfaces a 409 completeness-gate rejection as an ApiError with the German messages", async () => {
    installFetchMock({
      "/api/exams/7/reports/examination-office/pdf": () =>
        jsonResponse(409, {
          detail: { errors: ["1 Anmeldung ist noch unvollständig."] },
        }),
    });

    try {
      await downloadExaminationOfficePdf(7);
      throw new Error("expected downloadExaminationOfficePdf to throw");
    } catch (error) {
      expect(error).toBeInstanceOf(ApiError);
      expect((error as ApiError).messages).toEqual(["1 Anmeldung ist noch unvollständig."]);
    }
  });
});

describe("downloadExaminationOfficeExcel", () => {
  it("requests the Excel media type and falls back to pruefungsamt.xlsx", async () => {
    const excelBlob = new Blob(["PK"], { type: EXCEL_MEDIA_TYPE });
    const mock = installFetchMock({
      "/api/exams/7/reports/examination-office/excel": () => blobResponse(200, excelBlob),
    });

    const result = await downloadExaminationOfficeExcel(7);
    expect(result.blob).toBe(excelBlob);
    expect(result.filename).toBe("pruefungsamt.xlsx");
    const init = mock.mock.calls[0]?.[1];
    const headers = init?.headers as Record<string, string>;
    expect(headers.Accept).toBe(EXCEL_MEDIA_TYPE);
  });
});

describe("downloadStudentResultsPdf", () => {
  it("GETs the student-results PDF path and falls back to notenliste.pdf", async () => {
    const pdfBlob = new Blob(["%PDF-1.4"], { type: "application/pdf" });
    const mock = installFetchMock({
      "/api/exams/7/reports/student-results/pdf": () => blobResponse(200, pdfBlob),
    });

    const result = await downloadStudentResultsPdf(7);
    expect(result.filename).toBe("notenliste.pdf");
    expect(String(mock.mock.calls[0]?.[0])).toContain("/api/exams/7/reports/student-results/pdf");
    const init = mock.mock.calls[0]?.[1];
    const headers = init?.headers as Record<string, string>;
    expect(headers.Accept).toBe("application/pdf");
  });
});

describe("downloadStudentResultsExcel", () => {
  it("requests the Excel media type and falls back to notenliste.xlsx", async () => {
    const excelBlob = new Blob(["PK"], { type: EXCEL_MEDIA_TYPE });
    const mock = installFetchMock({
      "/api/exams/7/reports/student-results/excel": () => blobResponse(200, excelBlob),
    });

    const result = await downloadStudentResultsExcel(7);
    expect(result.blob).toBe(excelBlob);
    expect(result.filename).toBe("notenliste.xlsx");
    const init = mock.mock.calls[0]?.[1];
    const headers = init?.headers as Record<string, string>;
    expect(headers.Accept).toBe(EXCEL_MEDIA_TYPE);
  });

  it("throws an ApiError on a non-2xx response instead of returning a blob", async () => {
    installFetchMock({
      "/api/exams/7/reports/student-results/excel": () =>
        jsonResponse(404, { detail: "Nicht gefunden." }),
    });

    await expect(downloadStudentResultsExcel(7)).rejects.toBeInstanceOf(ApiError);
  });
});
