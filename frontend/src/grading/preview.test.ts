import { describe, expect, it } from "vitest";

import {
  GRADE_SCALE,
  compareDecimalStrings,
  computeGradePreview,
  exercisePointsFieldError,
  fromScaled,
  gradeFromServerMessage,
  schemaFieldErrors,
  sumMaxPoints,
  thresholdPointsPreview,
  toScaled,
  validateExercises,
  validateGradingSchema,
  type GradeThresholdRow,
} from "./preview";

/** Builds a full ten-grade schema from a list of percentages, in §7.1 order. */
function schema(percentages: readonly string[]): { grade: string; percentage: string }[] {
  return GRADE_SCALE.map((grade, index) => ({
    grade,
    percentage: percentages[index] ?? "0",
  }));
}

const VALID_PERCENTAGES = ["95", "90", "85", "80", "75", "70", "65", "60", "55", "50"];

describe("scale conversion", () => {
  it("converts decimal strings to hundredths without touching the number type", () => {
    expect(toScaled("60")).toBe(6000n);
    expect(toScaled("12.5")).toBe(1250n);
    expect(toScaled("12.50")).toBe(1250n);
    expect(toScaled("0.75")).toBe(75n);
    expect(toScaled("95")).toBe(9500n);
  });

  it("rejects non-decimals", () => {
    expect(toScaled("abc")).toBeNull();
    expect(toScaled("12,5")).toBeNull(); // comma form must be parsed by format.ts first
    expect(toScaled("1e3")).toBeNull();
    expect(toScaled("")).toBeNull();
  });

  it("round-trips back to a fixed two-decimal string", () => {
    expect(fromScaled(5700n)).toBe("57.00");
    expect(fromScaled(75n)).toBe("0.75");
    expect(fromScaled(0n)).toBe("0.00");
    expect(fromScaled(5n)).toBe("0.05");
  });
});

describe("sumMaxPoints", () => {
  it("adds exercise max points exactly", () => {
    expect(sumMaxPoints(["12.50", "0.75"])).toBe("13.25");
    expect(sumMaxPoints(["20", "20", "20"])).toBe("60.00");
    expect(sumMaxPoints([])).toBe("0.00");
  });

  it("is exact where binary floats are not (0.1 + 0.2)", () => {
    expect(sumMaxPoints(["0.1", "0.2"])).toBe("0.30");
  });

  it("returns null while a row is still invalid", () => {
    expect(sumMaxPoints(["12.5", ""])).toBeNull();
    expect(sumMaxPoints(["12.5", "abc"])).toBeNull();
  });
});

describe("thresholdPointsPreview (SPECIFICATION.md 7.2 / 7.5)", () => {
  it("reproduces the 7.5 worked example", () => {
    expect(thresholdPointsPreview("95", "60")).toBe("57.00");
    expect(thresholdPointsPreview("50", "60")).toBe("30.00");
  });

  it("floors to the nearest 0.5 points", () => {
    // 62 % of 60 = 37.2 -> floor to 37.0
    expect(thresholdPointsPreview("62", "60")).toBe("37.00");
    // 63 % of 60 = 37.8 -> floor to 37.5
    expect(thresholdPointsPreview("63", "60")).toBe("37.50");
    // 60 % of 45 = 27.0 exactly — the value that is 27.000000000000004 in IEEE-754 doubles
    // (7.0 names this exact case), so a float implementation could floor it to 26.5.
    expect(thresholdPointsPreview("60", "45")).toBe("27.00");
  });

  it("handles 0 and 100 percent", () => {
    expect(thresholdPointsPreview("0", "60")).toBe("0.00");
    expect(thresholdPointsPreview("100", "60")).toBe("60.00");
  });

  it("returns null for unusable input instead of a plausible wrong number", () => {
    expect(thresholdPointsPreview("95", "")).toBeNull();
    expect(thresholdPointsPreview("", "60")).toBeNull();
    expect(thresholdPointsPreview("abc", "60")).toBeNull();
  });
});

describe("compareDecimalStrings", () => {
  it("compares numerically, not lexicographically", () => {
    // The trap: "9" > "10" as strings.
    expect(compareDecimalStrings("9", "10")).toBe(-1);
    expect(compareDecimalStrings("100", "99.9")).toBe(1);
    expect(compareDecimalStrings("12.50", "12.5")).toBe(0);
  });
});

describe("validateGradingSchema (SPECIFICATION.md 7.2)", () => {
  it("accepts a strictly decreasing schema", () => {
    expect(validateGradingSchema(schema(VALID_PERCENTAGES))).toEqual([]);
  });

  it("rejects an equal pair (decreasing must be strict)", () => {
    const errors = validateGradingSchema(
      schema(["95", "90", "85", "80", "75", "70", "65", "60", "55", "55"]),
    );
    expect(errors.length).toBe(1);
    expect(errors[0]).toContain("streng fallend");
    expect(errors[0]).toContain("3,7");
    expect(errors[0]).toContain("4,0");
  });

  it("rejects an increasing pair", () => {
    const errors = validateGradingSchema(
      schema(["95", "90", "85", "80", "75", "70", "65", "60", "40", "50"]),
    );
    expect(errors.length).toBe(1);
    expect(errors[0]).toContain("streng fallend");
  });

  it("catches the lexicographic trap: 9 % after 10 % is still decreasing", () => {
    const errors = validateGradingSchema(
      schema(["95", "90", "85", "80", "75", "70", "65", "60", "10", "9"]),
    );
    expect(errors).toEqual([]);
  });

  it("rejects invalid and out-of-range percentages", () => {
    const withGarbage = validateGradingSchema(
      schema(["95", "90", "85", "80", "75", "70", "65", "60", "55", "fünfzig"]),
    );
    expect(withGarbage.some((message) => message.includes("kein gültiger Prozentwert"))).toBe(true);

    const tooHigh = validateGradingSchema(
      schema(["101", "90", "85", "80", "75", "70", "65", "60", "55", "50"]),
    );
    expect(tooHigh.some((message) => message.includes("zwischen 0 und 100"))).toBe(true);
  });

  it("rejects exactly 0 %, mirroring the backend's (0, 100] range (app/grading/schema.py)", () => {
    const errors = validateGradingSchema(
      schema(["95", "90", "85", "80", "75", "70", "65", "60", "55", "0"]),
    );
    expect(errors.some((message) => message.includes("zwischen 0 und 100"))).toBe(true);
  });

  it("requires exactly the ten grades of 7.1 in order", () => {
    const missing = validateGradingSchema(schema(VALID_PERCENTAGES).slice(0, 9));
    expect(missing.some((message) => message.includes("zehn Noten"))).toBe(true);

    const reordered = schema(VALID_PERCENTAGES).slice().reverse();
    expect(validateGradingSchema(reordered).some((m) => m.includes("zehn Noten"))).toBe(true);
  });
});

describe("validateExercises", () => {
  it("accepts a normal exercise list", () => {
    expect(
      validateExercises([
        { name: "Aufgabe 1", max_points: "12.5" },
        { name: "Aufgabe 2", max_points: "0.75" },
      ]),
    ).toEqual([]);
  });

  it("rejects empty names and unusable point values", () => {
    const errors = validateExercises([
      { name: "  ", max_points: "10" },
      { name: "Aufgabe 2", max_points: "x" },
      { name: "Aufgabe 3", max_points: "0" },
    ]);
    expect(errors.length).toBe(3);
    expect(errors[0]).toContain("Bezeichnung");
    expect(errors[1]).toContain("keine gültige Punktzahl");
    expect(errors[2]).toContain("größer als 0");
  });
});

describe("exercisePointsFieldError (Fix 2: per-field marking)", () => {
  it("accepts a normal positive decimal", () => {
    expect(exercisePointsFieldError("12.5")).toBeNull();
    expect(exercisePointsFieldError("0.75")).toBeNull();
  });

  it("does not flag an empty field — a normal mid-edit state, not an error (Fix 1)", () => {
    expect(exercisePointsFieldError("")).toBeNull();
    expect(exercisePointsFieldError("   ")).toBeNull();
  });

  it("flags non-numeric text", () => {
    expect(exercisePointsFieldError("abc")).toContain("keine gültige Punktzahl");
  });

  it("does not flag a value mid-keystroke ('12,' on the way to '12,5')", () => {
    expect(exercisePointsFieldError("12,")).toBeNull();
    expect(exercisePointsFieldError("62.")).toBeNull();
  });

  it("flags a value that is not strictly greater than 0", () => {
    expect(exercisePointsFieldError("0")).toContain("größer als 0");
    expect(exercisePointsFieldError("-5")).toContain("größer als 0");
  });
});

describe("schemaFieldErrors (Fix 2: per-field marking)", () => {
  it("flags nothing for a valid, strictly-decreasing schema", () => {
    expect(schemaFieldErrors(schema(VALID_PERCENTAGES)).size).toBe(0);
  });

  it("marks the later (worse) grade of a decreasing violation, not the earlier one", () => {
    // 2.7 (index 5) raised to 80, no longer lower than 2.3's 75 (index 4).
    const percentages = ["95", "90", "85", "80", "75", "80", "65", "60", "55", "50"];
    const errors = schemaFieldErrors(schema(percentages));
    expect(errors.get("2.7")).toContain("2,3");
    expect(errors.has("2.3")).toBe(false);
  });

  it("marks an individually out-of-range value (0 or 101) on its own field only", () => {
    const zero = schemaFieldErrors(
      schema(["95", "90", "85", "80", "75", "70", "65", "60", "55", "0"]),
    );
    expect(zero.get("4.0")).toContain("größer als 0");
    expect(zero.has("3.7")).toBe(false);

    const tooHigh = schemaFieldErrors(
      schema(["101", "90", "85", "80", "75", "70", "65", "60", "55", "50"]),
    );
    expect(tooHigh.get("1.0")).toContain("100");
    expect(tooHigh.has("1.3")).toBe(false);
  });

  it("marks an empty field, but only that field (no spurious pairwise message)", () => {
    const errors = schemaFieldErrors(schema(["95", "90", "85", "80", "75", "", "65", "60", "55", "50"]));
    expect(errors.get("2.7")).toContain("Prozentwert");
    expect(errors.has("3.0")).toBe(false);
  });

  it("does not flag a percentage mid-keystroke ('62,' on the way to '62,5')", () => {
    const errors = schemaFieldErrors(
      schema(["95", "90", "85", "80", "75", "70", "65", "60", "55", "62,"]),
    );
    expect(errors.has("4.0")).toBe(false);
  });

  it("flags a value already invalid on its own without also adding the pairwise message", () => {
    const errors = schemaFieldErrors(
      schema(["95", "90", "85", "80", "75", "abc", "65", "60", "55", "50"]),
    );
    expect(errors.size).toBe(1);
    expect(errors.get("2.7")).toContain("kein gültiger Prozentwert");
  });
});

describe("gradeFromServerMessage (Fix 2: highlighting a 422's own field)", () => {
  it("finds the single grade named in an individual-value message", () => {
    expect(
      gradeFromServerMessage(
        "Prozentwert für Note 4.0 muss größer als 0 und höchstens 100 sein (aktuell: 0,00 %).",
      ),
    ).toBe("4.0");
  });

  it("finds the worse (second) grade named in a strictly-decreasing message", () => {
    expect(
      gradeFromServerMessage(
        "Prozentwerte müssen von 1.0 bis 4.0 streng fallend sein: Note 2.3 (75,00 %) muss " +
          "einen höheren Prozentwert haben als Note 2.7 (80,00 %).",
      ),
    ).toBe("2.7");
  });

  it("returns null for a message naming no grade, so the caller falls back to the summary", () => {
    expect(gradeFromServerMessage("Der Server ist nicht erreichbar.")).toBeNull();
  });
});

describe("computeGradePreview (§7.3/§7.4, §7.5 worked example)", () => {
  // §7.5: max_points = 60, 1.0 at 95 % -> 57.0, 4.0 (pass) at 50 % -> 30.0.
  const SCHEMA_60: GradeThresholdRow[] = [
    { grade: "1.0", threshold_points: "57.00" },
    { grade: "4.0", threshold_points: "30.00" },
  ];

  it("30.0 exactly meets the 4.0 threshold -> grade 4.0", () => {
    const result = computeGradePreview({
      enteredExercisePoints: ["30.00"],
      bonusPoints: "0.00",
      bonusMode: "ALWAYS",
      attended: true,
      gradingSchema: SCHEMA_60,
      gradingConfigured: true,
    });
    expect(result.rawTotal).toBe("30.00");
    expect(result.finalTotal).toBe("30.00");
    expect(result.gradeLabel).toBe("4,0");
  });

  it("29.5 is below 30.0 -> nicht bestanden", () => {
    const result = computeGradePreview({
      enteredExercisePoints: ["29.50"],
      bonusPoints: "0.00",
      bonusMode: "ALWAYS",
      attended: true,
      gradingSchema: SCHEMA_60,
      gradingConfigured: true,
    });
    expect(result.gradeLabel).toBe("nicht bestanden");
  });

  it("attended=false overrides everything else -> n.e., final_total null", () => {
    const result = computeGradePreview({
      enteredExercisePoints: ["29.50"],
      bonusPoints: "0.00",
      bonusMode: "ALWAYS",
      attended: false,
      gradingSchema: SCHEMA_60,
      gradingConfigured: true,
    });
    expect(result.gradeLabel).toBe("n.e.");
    expect(result.finalTotal).toBeNull();
    // raw_total is still shown even though the grade doesn't use it.
    expect(result.rawTotal).toBe("29.50");
  });

  it("ALWAYS applies bonus unconditionally: 28.0 + 3 clears 30.0 -> grade 4.0", () => {
    const result = computeGradePreview({
      enteredExercisePoints: ["28.00"],
      bonusPoints: "3",
      bonusMode: "ALWAYS",
      attended: true,
      gradingSchema: SCHEMA_60,
      gradingConfigured: true,
    });
    expect(result.finalTotal).toBe("31.00");
    expect(result.gradeLabel).toBe("4,0");
  });

  it("ONLY_IF_PASSING_WITHOUT_BONUS withholds bonus when raw_total alone fails", () => {
    const result = computeGradePreview({
      enteredExercisePoints: ["28.00"],
      bonusPoints: "3",
      bonusMode: "ONLY_IF_PASSING_WITHOUT_BONUS",
      attended: true,
      gradingSchema: SCHEMA_60,
      gradingConfigured: true,
    });
    // Bonus is NOT applied: final_total stays at raw_total, and the grade stays a fail.
    expect(result.finalTotal).toBe("28.00");
    expect(result.gradeLabel).toBe("nicht bestanden");
  });

  it("ONLY_IF_PASSING_WITHOUT_BONUS still applies (and can improve) the grade once raw_total passes", () => {
    const fullSchema: GradeThresholdRow[] = [
      { grade: "1.0", threshold_points: "57.00" },
      { grade: "1.3", threshold_points: "54.00" },
      { grade: "1.7", threshold_points: "51.00" },
      { grade: "2.0", threshold_points: "48.00" },
      { grade: "2.3", threshold_points: "45.00" },
      { grade: "2.7", threshold_points: "42.00" },
      { grade: "3.0", threshold_points: "39.00" },
      { grade: "3.3", threshold_points: "36.00" },
      { grade: "3.7", threshold_points: "32.00" },
      { grade: "4.0", threshold_points: "30.00" },
    ];
    const result = computeGradePreview({
      enteredExercisePoints: ["32.00"],
      bonusPoints: "3",
      bonusMode: "ONLY_IF_PASSING_WITHOUT_BONUS",
      attended: true,
      gradingSchema: fullSchema,
      gradingConfigured: true,
    });
    expect(result.finalTotal).toBe("35.00"); // 32.0 raw_total already passes -> bonus applies
    expect(result.gradeLabel).toBe("3,7");
  });

  it("attended=null (not yet recorded) previews no grade, distinct from n.e. or nicht bestanden", () => {
    const result = computeGradePreview({
      enteredExercisePoints: ["45.00"],
      bonusPoints: "0.00",
      bonusMode: "ALWAYS",
      attended: null,
      gradingSchema: SCHEMA_60,
      gradingConfigured: true,
    });
    expect(result.gradeLabel).toBe("—");
    expect(result.gradeLabel).not.toBe("n.e.");
    expect(result.gradeLabel).not.toBe("nicht bestanden");
  });

  it("an unconfigured grading schema never produces a confident-looking grade", () => {
    const result = computeGradePreview({
      enteredExercisePoints: ["45.00"],
      bonusPoints: "0.00",
      bonusMode: "ALWAYS",
      attended: true,
      gradingSchema: [],
      gradingConfigured: false,
    });
    expect(result.gradeLabel).toBe("—");
  });

  it("is exact where binary floats are not: 0.1 + 0.2 style bonus addition", () => {
    const result = computeGradePreview({
      enteredExercisePoints: ["12.50"],
      bonusPoints: "0.25",
      bonusMode: "ALWAYS",
      attended: true,
      gradingSchema: [],
      gradingConfigured: false,
    });
    expect(result.finalTotal).toBe("12.75");
  });
});
