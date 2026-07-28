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

import { formatDecimal, isCanonicalDecimal } from "../util/format";

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
    if (scaled < 0n || scaled > 100n * SCALE_FACTOR) {
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
