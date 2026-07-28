import { describe, expect, it } from "vitest";

import {
  EMPTY_DISPLAY,
  formatDate,
  formatDateOrDash,
  formatDecimal,
  formatDecimalOrDash,
  formatPercent,
  isCanonicalDecimal,
  parseDateInput,
  parseDecimalInput,
} from "./format";

describe("formatDecimal", () => {
  it("uses the German comma separator", () => {
    expect(formatDecimal("12.5")).toBe("12,5");
    expect(formatDecimal("1.3")).toBe("1,3");
    expect(formatDecimal("95")).toBe("95");
  });

  it("preserves trailing zeros, which a JS number would drop", () => {
    expect(formatDecimal("12.50")).toBe("12,50");
    expect(formatDecimal("0.70")).toBe("0,70");
    expect(formatDecimal("57.00")).toBe("57,00");
  });

  it("preserves values that no JS double can represent exactly", () => {
    expect(formatDecimal("0.1")).toBe("0,1");
    expect(formatDecimal("123456789012345678901234567890.125")).toBe(
      "123456789012345678901234567890,125",
    );
  });

  it("renders absent values as an em dash", () => {
    expect(formatDecimalOrDash(null)).toBe(EMPTY_DISPLAY);
    expect(formatDecimalOrDash(undefined)).toBe(EMPTY_DISPLAY);
    expect(formatDecimalOrDash("")).toBe(EMPTY_DISPLAY);
    expect(formatDecimalOrDash("12.50")).toBe("12,50");
  });

  it("appends a percent sign", () => {
    expect(formatPercent("62.5")).toBe("62,5\u00A0%");
  });
});

describe("parseDecimalInput", () => {
  it("accepts both the German comma and the dot form", () => {
    expect(parseDecimalInput("12,5")).toBe("12.5");
    expect(parseDecimalInput("12.5")).toBe("12.5");
  });

  it("round-trips through formatDecimal without changing the digits", () => {
    for (const canonical of ["0.75", "12.50", "57.00", "95", "0.1", "100.00"]) {
      const german = formatDecimal(canonical);
      expect(parseDecimalInput(german)).toBe(canonical);
    }
  });

  it("keeps trailing zeros instead of normalising them away", () => {
    expect(parseDecimalInput("12,50")).toBe("12.50");
    expect(parseDecimalInput("3,000")).toBe("3.000");
  });

  it("trims blanks and completes a bare fraction", () => {
    expect(parseDecimalInput("  7,25  ")).toBe("7.25");
    expect(parseDecimalInput(",5")).toBe("0.5");
    expect(parseDecimalInput(".5")).toBe("0.5");
  });

  it("rejects garbage", () => {
    for (const garbage of [
      "",
      "   ",
      "abc",
      "12,5,5",
      "12.5.5",
      "1e3",
      "12,",
      "12.",
      ",",
      "-",
      "+",
      "1 2",
      "12,5 Punkte",
      "NaN",
      "Infinity",
      "０,５",
    ]) {
      expect(parseDecimalInput(garbage), `expected ${JSON.stringify(garbage)} to be rejected`).toBeNull();
    }
  });

  it("rejects negatives unless explicitly allowed", () => {
    expect(parseDecimalInput("-1,5")).toBeNull();
    expect(parseDecimalInput("-1,5", { allowNegative: true })).toBe("-1.5");
  });

  it("only ever emits canonical decimals", () => {
    for (const input of ["12,5", ",5", "0,75", "100"]) {
      const parsed = parseDecimalInput(input);
      expect(parsed).not.toBeNull();
      expect(isCanonicalDecimal(parsed as string)).toBe(true);
    }
  });
});

describe("formatDate", () => {
  it("formats an ISO date the German way", () => {
    expect(formatDate("2024-02-15")).toBe("15.02.2024");
    expect(formatDate("2023-12-01")).toBe("01.12.2023");
  });

  it("accepts a full ISO timestamp, as created_at is", () => {
    expect(formatDate("2024-02-15T09:31:00Z")).toBe("15.02.2024");
  });

  it("renders absent or unparseable dates as empty / em dash", () => {
    expect(formatDate(null)).toBe("");
    expect(formatDate(undefined)).toBe("");
    expect(formatDate("15.02.2024")).toBe("");
    expect(formatDateOrDash(null)).toBe(EMPTY_DISPLAY);
    expect(formatDateOrDash("2024-02-15")).toBe("15.02.2024");
  });
});

describe("parseDateInput", () => {
  it("converts German input to the wire format", () => {
    expect(parseDateInput("15.02.2024")).toBe("2024-02-15");
    expect(parseDateInput("1.3.2024")).toBe("2024-03-01");
    expect(parseDateInput(" 15.02.2024 ")).toBe("2024-02-15");
  });

  it("round-trips with formatDate", () => {
    expect(parseDateInput(formatDate("2024-02-15"))).toBe("2024-02-15");
  });

  it("passes an already-ISO value through", () => {
    expect(parseDateInput("2024-02-15")).toBe("2024-02-15");
  });

  it("rejects garbage and impossible field values", () => {
    for (const garbage of ["", "morgen", "15.02.24", "32.01.2024", "15.13.2024", "2024-13-01"]) {
      expect(parseDateInput(garbage), `expected ${JSON.stringify(garbage)} to be rejected`).toBeNull();
    }
  });
});
