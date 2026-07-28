/**
 * The ONLY module in this frontend that does arithmetic on points or percentages.
 *
 * Why it exists: the exam detail response carries `exercises[].max_points` and
 * `grading_schema[].percentage`, but no server-computed total and no per-grade point
 * threshold. To show the instructor what a schema edit actually means before saving, the UI
 * has to compute them — so this module does, and every value it produces is labelled a
 * *preview* (`SCHWELLE_PREVIEW_HINT`). The authoritative numbers are always the backend's
 * (§7.2); nothing computed here is ever sent to the API.
 *
 * How it stays exact (§7.0): all arithmetic is `bigint` on a **fixed scale of 2 decimals**,
 * i.e. values are carried as hundredths. There is no `number` anywhere in the computation
 * path, so IEEE-754 rounding (`0.6 * 45 === 27.000000000000004`) cannot occur. Decimal
 * strings are converted to hundredths by string surgery, never by `Number()`/`parseFloat`.
 */

import type { BonusMode } from "../api/client";
import { EMPTY_DISPLAY, formatDecimal, isCanonicalDecimal } from "../util/format";

/** Number of decimal places every value is scaled to internally. */
const SCALE = 2;
/** 10 ** SCALE, as a bigint. */
const SCALE_FACTOR = 100n;
/** Threshold rounding step of §7.2 (0.5 points) expressed in hundredths. */
const HALF_POINT = 50n;

/** §7.1: the ten passing grades, best to worst. Strings, never JSON numbers. */
export const GRADE_SCALE = [
  "1.0",
  "1.3",
  "1.7",
  "2.0",
  "2.3",
  "2.7",
  "3.0",
  "3.3",
  "3.7",
  "4.0",
] as const;

export type Grade = (typeof GRADE_SCALE)[number];

export const SCHWELLE_PREVIEW_HINT =
  "Vorschau — die verbindliche Berechnung erfolgt auf dem Server.";

/**
 * "12.5" -> 1250n, "0.75" -> 75n, "60" -> 6000n.
 * Returns null if the string is not a canonical decimal. A fraction longer than SCALE digits
 * is truncated (not rounded) — that only happens for percentages typed with more than two
 * decimals, and this is a preview, so truncating is the conservative direction.
 */
export function toScaled(value: string): bigint | null {
  if (!isCanonicalDecimal(value)) return null;

  const negative = value.startsWith("-");
  const unsigned = negative ? value.slice(1) : value;
  const [intPart = "", fracRaw = ""] = unsigned.split(".");
  const frac = fracRaw.slice(0, SCALE).padEnd(SCALE, "0");

  const digits = `${intPart}${frac}`;
  // BigInt() on a pure digit string is exact for any length — unlike Number(), which is what
  // this whole module exists to avoid.
  const magnitude = BigInt(digits);
  return negative ? -magnitude : magnitude;
}

/** 5700n -> "57.00". Inverse of toScaled, always emitting exactly SCALE decimals. */
export function fromScaled(scaled: bigint): string {
  const negative = scaled < 0n;
  const magnitude = negative ? -scaled : scaled;
  const digits = magnitude.toString().padStart(SCALE + 1, "0");
  const cut = digits.length - SCALE;
  const intPart = digits.slice(0, cut);
  const frac = digits.slice(cut);
  return `${negative ? "-" : ""}${intPart}.${frac}`;
}

/**
 * Sum of the exercises' max points, e.g. ["12.50", "0.75"] -> "13.25".
 * Returns null as soon as one value is not a valid decimal, so a half-typed row never yields a
 * plausible-looking wrong total.
 */
export function sumMaxPoints(values: readonly string[]): string | null {
  let total = 0n;
  for (const value of values) {
    const scaled = toScaled(value);
    if (scaled === null) return null;
    total += scaled;
  }
  return fromScaled(total);
}

/**
 * §7.2: threshold_points(grade) = floor( (percentage / 100 * max_points) / 0.5 ) * 0.5
 *
 * In hundredths: raw = pct * max / 10000 (SCALE_FACTOR twice — once for each operand's
 * scale), then floor to a multiple of 0.5 via (raw / 50) * 50.
 *
 * Both divisions are bigint divisions, which truncate toward zero. For non-negative values
 * truncation *is* floor, and floor(floor(x)/n) === floor(x/n), so the two-step truncation is
 * exactly the single floor the spec asks for. Do not "simplify" this to a float expression.
 *
 * §7.5 acceptance values: max 60, 95 % -> "57.00"; max 60, 50 % -> "30.00".
 */
export function thresholdPointsPreview(
  percentage: string,
  maxPointsTotal: string,
): string | null {
  const pct = toScaled(percentage);
  const max = toScaled(maxPointsTotal);
  if (pct === null || max === null) return null;
  if (pct < 0n || max < 0n) return null;

  const rawHundredths = (pct * max) / (SCALE_FACTOR * SCALE_FACTOR);
  const flooredToHalfPoint = (rawHundredths / HALF_POINT) * HALF_POINT;
  return fromScaled(flooredToHalfPoint);
}

/**
 * Compare two decimal strings exactly. Returns -1/0/1, or null if either side is unparseable.
 * Exists because the obvious `a > b` on strings compares lexicographically, where "9" > "10"
 * — the single most likely silent bug in the strictly-decreasing check below.
 */
export function compareDecimalStrings(a: string, b: string): -1 | 0 | 1 | null {
  const left = toScaled(a);
  const right = toScaled(b);
  if (left === null || right === null) return null;
  if (left < right) return -1;
  if (left > right) return 1;
  return 0;
}

export interface SchemaRowInput {
  grade: string;
  percentage: string;
}

/**
 * Client-side mirror of §7.2's validation, purely as a convenience so the instructor sees the
 * problem before saving. It is NOT authoritative: the server re-validates and its German 422
 * messages are always displayed verbatim when they come back.
 *
 * Returns German messages, empty array = valid.
 */
export function validateGradingSchema(rows: readonly SchemaRowInput[]): string[] {
  const errors: string[] = [];

  const grades = rows.map((row) => row.grade);
  const expected = GRADE_SCALE.join(",");
  if (grades.join(",") !== expected) {
    errors.push(
      "Der Notenschlüssel muss genau die zehn Noten 1,0 bis 4,0 in dieser Reihenfolge enthalten.",
    );
  }

  for (const row of rows) {
    const scaled = toScaled(row.percentage);
    if (scaled === null) {
      errors.push(
        `Note ${formatDecimal(row.grade)}: „${row.percentage}“ ist kein gültiger Prozentwert.`,
      );
      continue;
    }
    // Mirrors the backend (`app/grading/schema.py`: `if value <= 0 or value > _HUNDRED`) — a
    // percentage of exactly 0 is rejected there, so accepting it here would let a field render
    // as valid client-side and only fail once the server is asked.
    if (scaled <= 0n || scaled > 100n * SCALE_FACTOR) {
      errors.push(
        `Note ${formatDecimal(row.grade)}: Der Prozentwert muss zwischen 0 und 100 liegen.`,
      );
    }
  }

  // Strictly decreasing from 1.0 down to 4.0: each better grade needs a strictly higher
  // percentage than the next one. Indexed access is checked because of noUncheckedIndexedAccess.
  for (let i = 0; i + 1 < rows.length; i += 1) {
    const better = rows[i];
    const worse = rows[i + 1];
    if (better === undefined || worse === undefined) continue;
    const cmp = compareDecimalStrings(better.percentage, worse.percentage);
    if (cmp === null) continue; // already reported as an invalid value above
    if (cmp <= 0) {
      errors.push(
        `Die Prozentwerte müssen streng fallend sein: Note ${formatDecimal(better.grade)} ` +
          `(${formatDecimal(better.percentage)} %) muss einen höheren Prozentwert haben als ` +
          `Note ${formatDecimal(worse.grade)} (${formatDecimal(worse.percentage)} %).`,
      );
    }
  }

  return errors;
}

/** Validation for the exercises editor, same "convenience only" caveat as above. */
export function validateExercises(
  rows: readonly { name: string; max_points: string }[],
): string[] {
  const errors: string[] = [];
  rows.forEach((row, index) => {
    const position = index + 1;
    if (row.name.trim() === "") {
      errors.push(`Aufgabe ${position}: Die Bezeichnung darf nicht leer sein.`);
    }
    const scaled = toScaled(row.max_points);
    if (scaled === null) {
      errors.push(`Aufgabe ${position}: „${row.max_points}“ ist keine gültige Punktzahl.`);
    } else if (scaled <= 0n) {
      errors.push(`Aufgabe ${position}: Die Punktzahl muss größer als 0 sein.`);
    }
  });
  return errors;
}

/* --------------------------------------------------------- per-field error markers (Fix 2) */

/**
 * "12," / "62." — a decimal separator was typed but no fraction digit follows it yet. This is
 * the shape `parseDecimalInput` produces mid-keystroke while typing e.g. "12,5" (it rejects an
 * incomplete separator on purpose — format.ts's "a separator was typed but no fraction followed
 * — incomplete, reject"), which would otherwise fall back to the raw text here and flash the
 * field red for one keystroke. Recognising it and treating it like "still typing" (no error, no
 * red border) keeps the per-field marking from doing exactly what Fix 1 exists to prevent for
 * the total: punishing the instructor for being mid-edit.
 */
const INCOMPLETE_ENTRY_RE = /^[+-]?\d*[.,]$/;

/**
 * Per-field message for a single exercise's max-points input, given the canonical-or-raw text
 * the same way `validateExercises` sees it (`parseDecimalInput(text) ?? text`) — or `null` if
 * the field is fine. An *empty* field, and a value still mid-keystroke (see
 * `INCOMPLETE_ENTRY_RE`), are deliberately not errors here: they are normal mid-edit states
 * (Fix 1 counts an empty one as 0 in the displayed total) and are still rejected on save by
 * `validateExercises`. Only genuinely unparseable text or a non-positive value is flagged.
 */
export function exercisePointsFieldError(maxPoints: string): string | null {
  const trimmed = maxPoints.trim();
  if (trimmed === "" || INCOMPLETE_ENTRY_RE.test(trimmed)) return null;
  const scaled = toScaled(maxPoints);
  if (scaled === null) return `„${maxPoints}“ ist keine gültige Punktzahl.`;
  if (scaled <= 0n) return "Die Punktzahl muss größer als 0 sein.";
  return null;
}

/**
 * Per-field messages for the ten grading-schema percentage inputs, keyed by grade, so the UI
 * can mark the exact offending input instead of only the summary block. `rows` is expected in
 * the same canonical-or-raw shape `validateGradingSchema` takes (`parseDecimalInput(text) ??
 * text`), in §7.1 order.
 *
 * Two kinds of problems, never both on the same field:
 *
 *  - an individually invalid value (empty / not a number / <=0 / >100) is attached to that
 *    grade's own field;
 *  - a strictly-decreasing violation (§7.2) between two adjacent grades is attached to the
 *    *worse* grade — the later, lower-quality one in the 1.0..4.0 order. This mirrors the
 *    user's own report ("2.7 has a higher percentage entered than 2.3, then mark the 2.7
 *    input"): reading top-down from 1.0 to 4.0, 2.7 is the row whose value broke a descent that
 *    had held up to that point, so it is the one to fix.
 *
 * A grade already flagged individually invalid never also gets the pairwise message — there is
 * nothing meaningful to compare an unparseable value against.
 */
export function schemaFieldErrors(rows: readonly SchemaRowInput[]): Map<string, string> {
  const errors = new Map<string, string>();

  for (const row of rows) {
    const trimmed = row.percentage.trim();
    if (trimmed === "") {
      errors.set(row.grade, "Bitte einen Prozentwert angeben.");
      continue;
    }
    if (INCOMPLETE_ENTRY_RE.test(trimmed)) continue; // still mid-keystroke, not an error yet
    const scaled = toScaled(row.percentage);
    if (scaled === null) {
      errors.set(row.grade, `„${row.percentage}“ ist kein gültiger Prozentwert.`);
      continue;
    }
    if (scaled <= 0n || scaled > 100n * SCALE_FACTOR) {
      errors.set(row.grade, "Muss größer als 0 und höchstens 100 sein.");
    }
  }

  for (let i = 0; i + 1 < rows.length; i += 1) {
    const better = rows[i];
    const worse = rows[i + 1];
    if (better === undefined || worse === undefined) continue;
    if (errors.has(worse.grade)) continue; // already individually invalid
    const cmp = compareDecimalStrings(better.percentage, worse.percentage);
    if (cmp === null) continue; // already reported as an invalid value above
    if (cmp <= 0) {
      errors.set(worse.grade, `Muss kleiner als bei Note ${formatDecimal(better.grade)} sein.`);
    }
  }

  return errors;
}

/** Grade-name pattern the backend actually emits (`app/grading/schema.py`): always the dot
 * form ("Note 2.7"), matching `GRADE_SCALE` exactly — no locale conversion needed. */
const SERVER_GRADE_RE = /Note (\d\.\d)/g;

/**
 * Pulls the grade(s) named in a server-returned §7.2 message so a `422` can mark the same input
 * the summary block already describes, instead of leaving the instructor to find it themselves.
 * Not authoritative parsing of a stable contract — just best-effort text mining of the backend's
 * existing German wording (`docs/api-contract.md`'s `{"detail": {"errors": [...]}}` shape is the
 * actual contract; this only reads the message *text* it carries).
 *
 * A single-grade message ("Prozentwert für Note 4.0 muss ...") names one field. The strictly-
 * decreasing message names two ("... Note 2.3 ... höheren Prozentwert ... als Note 2.7 ...");
 * by the same "later, worse grade is the offender" rule as `schemaFieldErrors` above, the
 * *last* grade mentioned is the one returned. Returns `null` if no known grade is found, so the
 * caller can fall back to the summary block.
 */
export function gradeFromServerMessage(message: string): Grade | null {
  const grades = [...message.matchAll(SERVER_GRADE_RE)]
    .map((match) => match[1])
    .filter(
      (grade): grade is Grade => grade !== undefined && (GRADE_SCALE as readonly string[]).includes(grade),
    );
  return grades[grades.length - 1] ?? null;
}

/* --------------------------------------------------------------- §8 points-entry preview */

/** One grading-schema row as needed for a grade preview — grade plus the server-computed
 * `threshold_points` (§7.2). Matches `PointsSchemaRow` from `api/client.ts`, kept as a separate,
 * narrower type here so this module has no non-type dependency on the API client. */
export interface GradeThresholdRow {
  grade: string;
  threshold_points: string;
}

export interface GradePreviewInput {
  /** Canonical decimal strings of every exercise the student has an entered (non-empty, parsable)
   * value for. An exercise with no value yet is simply absent from this list — it must never be
   * padded with a "0.00" entry, or the live total would silently imply the exercise was graded. */
  enteredExercisePoints: readonly string[];
  /** The bonus-points field's current text, canonical-or-raw. An unparsable/mid-keystroke value
   * (e.g. "3," while typing "3,5") is treated as 0 for this *preview only* — the same "don't go
   * blank while the instructor is mid-edit" convention as ExamDetailPage's Fix 1. Never affects
   * what is actually saved. */
  bonusPoints: string;
  bonusMode: BonusMode;
  /** `null` = attendance not yet recorded, `false` = "n.e.", `true` = graded normally (§7.4). */
  attended: boolean | null;
  gradingSchema: readonly GradeThresholdRow[];
  /** Mirrors `PointsGrid.grading_configured`. A schema that is absent or only partially filled
   * must never produce a confident-looking grade — gate on this flag, not merely on
   * `gradingSchema.length`, since a partial schema could still have some rows. */
  gradingConfigured: boolean;
}

export interface GradePreviewResult {
  /** DECIMAL string: sum of `enteredExercisePoints`. Always computed, even when not attended. */
  rawTotal: string;
  /** DECIMAL string per §7.3, or `null` when not attended (§7.4: "no points needed/used"). */
  finalTotal: string | null;
  /** One of: a formatted grade ("1,3"), "nicht bestanden", "n.e.", or EMPTY_DISPLAY when no
   * preview is possible yet (schema not configured, or attendance not yet recorded). */
  gradeLabel: string;
}

/**
 * Client-side preview mirroring §7.3/§7.4's rules exactly, computed entirely in bigint
 * hundredths (via `sumMaxPoints`/`toScaled`/`compareDecimalStrings`) — never a JS number. Always
 * a *preview*: the server recomputes and its `grade`/`final_total` are authoritative and replace
 * this the moment a save response comes back (see `PointsEntryPage`'s save handler).
 */
export function computeGradePreview(input: GradePreviewInput): GradePreviewResult {
  const rawTotal = sumMaxPoints(input.enteredExercisePoints) ?? "0.00";

  // §7.4: attendance overrides everything else, including a raw_total that would otherwise pass.
  if (input.attended === false) {
    return { rawTotal, finalTotal: null, gradeLabel: "n.e." };
  }

  // A mid-keystroke/invalid bonus value previews as 0 rather than making the row's total vanish;
  // it is never what actually gets sent (the save payload reads the raw text separately).
  const bonusCanonical = fromScaled(toScaled(input.bonusPoints) ?? 0n);

  const sortedSchema = [...input.gradingSchema].sort(
    (a, b) =>
      (GRADE_SCALE as readonly string[]).indexOf(a.grade) -
      (GRADE_SCALE as readonly string[]).indexOf(b.grade),
  );
  const passingRow = sortedSchema.find((row) => row.grade === "4.0");

  // §7.3: ALWAYS adds bonus unconditionally; ONLY_IF_PASSING_WITHOUT_BONUS adds it only once
  // raw_total alone already clears the 4.0 threshold (checked here, not against final_total —
  // getting that backwards would let bonus points turn a fail into a pass).
  let finalTotal: string;
  if (input.bonusMode === "ALWAYS") {
    finalTotal = sumMaxPoints([rawTotal, bonusCanonical]) ?? rawTotal;
  } else {
    const passesWithoutBonus =
      passingRow !== undefined &&
      (compareDecimalStrings(rawTotal, passingRow.threshold_points) ?? -1) >= 0;
    finalTotal = passesWithoutBonus
      ? (sumMaxPoints([rawTotal, bonusCanonical]) ?? rawTotal)
      : rawTotal;
  }

  if (input.attended === null || !input.gradingConfigured || sortedSchema.length === 0) {
    return { rawTotal, finalTotal, gradeLabel: EMPTY_DISPLAY };
  }

  // Best (numerically lowest) grade whose threshold is met, 1.0 down to 4.0; below the 4.0
  // threshold is "nicht bestanden", never a blank/undefined grade (§7.4).
  for (const row of sortedSchema) {
    const cmp = compareDecimalStrings(finalTotal, row.threshold_points);
    if (cmp !== null && cmp >= 0) {
      return { rawTotal, finalTotal, gradeLabel: formatDecimal(row.grade) };
    }
  }
  return { rawTotal, finalTotal, gradeLabel: "nicht bestanden" };
}
