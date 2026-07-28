/**
 * German presentation formatting and the inverse input parsers (SPECIFICATION.md §14 #6).
 *
 * Hard rule for this whole file: **no `Number()`, `parseFloat`, `parseInt` or arithmetic on a
 * decimal value anywhere.** Points, percentages and thresholds are exact decimals (§7.0); the
 * moment one of them touches the JS number type it becomes an IEEE-754 double and a trailing
 * zero ("12.50") or an exact boundary value is silently lost. Everything here is pure string
 * surgery: a decimal goes in as a string and comes out as a string.
 */

/** Canonical wire form: optional sign, digits, optional dot-fraction. No exponent, no spaces. */
const CANONICAL_DECIMAL_RE = /^-?(?:\d+)(?:\.\d+)?$/;

/** Accepted user input: German comma or dot separator, optional leading/trailing blanks. */
const INPUT_DECIMAL_RE = /^([+-]?)(\d*)(?:[.,](\d*))?$/;

const ISO_DATE_RE = /^(\d{4})-(\d{2})-(\d{2})$/;
const GERMAN_DATE_RE = /^(\d{1,2})\.(\d{1,2})\.(\d{4})$/;

/** Shown where a value is absent (null exam_date, empty threshold, ...). */
export const EMPTY_DISPLAY = "—";

/**
 * "12.5" -> "12,5", "12.50" -> "12,50", "0.75" -> "0,75".
 * String in, string out: the fraction is never renormalized, so trailing zeros survive.
 * A value that is not a canonical decimal is returned unchanged rather than mangled — it
 * came from the server and showing it verbatim is more honest than hiding it.
 */
export function formatDecimal(value: string): string {
  if (!CANONICAL_DECIMAL_RE.test(value)) return value;
  return value.replace(".", ",");
}

/** Same as formatDecimal but renders null/undefined/"" as an em dash. */
export function formatDecimalOrDash(value: string | null | undefined): string {
  if (value === null || value === undefined || value === "") return EMPTY_DISPLAY;
  return formatDecimal(value);
}

/** formatDecimal plus a non-breaking-space percent sign: "95" -> "95 %". */
export function formatPercent(value: string): string {
  return `${formatDecimal(value)}\u00A0%`;
}

/**
 * Inverse of formatDecimal for form input. Accepts both "12,5" and "12.5" and returns the
 * canonical dot-decimal string the API expects, or `null` if the input is not a valid decimal.
 *
 * The digits themselves are passed through untouched (no padding, no stripping), so what the
 * instructor typed is what the backend's `Decimal(str)` sees.
 */
export function parseDecimalInput(
  input: string,
  options: { allowNegative?: boolean } = {},
): string | null {
  const trimmed = input.trim();
  if (trimmed === "") return null;

  const match = INPUT_DECIMAL_RE.exec(trimmed);
  if (match === null) return null;

  const sign = match[1] ?? "";
  const intPart = match[2] ?? "";
  const fracPart = match[3];

  // At least one digit somewhere; "," / "." / "-" alone are not numbers.
  if (intPart === "" && (fracPart === undefined || fracPart === "")) return null;
  // A separator was typed but no fraction followed ("12,") — incomplete, reject.
  if (fracPart !== undefined && fracPart === "") return null;
  if (sign === "-" && options.allowNegative !== true) return null;

  const negative = sign === "-";
  const integer = intPart === "" ? "0" : intPart;
  const canonical =
    (negative ? "-" : "") + integer + (fracPart === undefined ? "" : `.${fracPart}`);

  // Guard against "-0.0" style values slipping through as negative zero notation is harmless,
  // but re-validate the result so nothing but a canonical decimal can ever leave this function.
  return CANONICAL_DECIMAL_RE.test(canonical) ? canonical : null;
}

/** True if `value` is already a canonical dot-decimal string (what the API sends us). */
export function isCanonicalDecimal(value: string): boolean {
  return CANONICAL_DECIMAL_RE.test(value);
}

/**
 * "2024-02-15" -> "15.02.2024". Also accepts a full ISO timestamp
 * ("2024-02-15T09:31:00Z", as `created_at` is likely to be) by using its date part.
 * Returns "" for null/undefined/unparseable input.
 */
export function formatDate(value: string | null | undefined): string {
  if (value === null || value === undefined) return "";
  const datePart = value.split("T")[0] ?? "";
  const match = ISO_DATE_RE.exec(datePart);
  if (match === null) return "";
  const [, year, month, day] = match;
  if (year === undefined || month === undefined || day === undefined) return "";
  return `${day}.${month}.${year}`;
}

/** formatDate, but an absent date renders as an em dash instead of "". */
export function formatDateOrDash(value: string | null | undefined): string {
  const formatted = formatDate(value);
  return formatted === "" ? EMPTY_DISPLAY : formatted;
}

/**
 * Inverse of formatDate for form input: "15.02.2024" -> "2024-02-15" (the wire format).
 * Also accepts an already-ISO "2024-02-15" so pasting a value back in works.
 * Returns `null` for anything else. Day/month are zero-padded; calendar plausibility beyond
 * the 1–31 / 1–12 ranges is the backend's business.
 */
export function parseDateInput(input: string): string | null {
  const trimmed = input.trim();
  if (trimmed === "") return null;

  const iso = ISO_DATE_RE.exec(trimmed);
  if (iso !== null) {
    return isPlausible(iso[3], iso[2]) ? trimmed : null;
  }

  const german = GERMAN_DATE_RE.exec(trimmed);
  if (german === null) return null;
  const [, day, month, year] = german;
  if (day === undefined || month === undefined || year === undefined) return null;
  if (!isPlausible(day, month)) return null;
  return `${year}-${month.padStart(2, "0")}-${day.padStart(2, "0")}`;
}

const DAY_RE = /^(?:0?[1-9]|[12]\d|3[01])$/;
const MONTH_RE = /^(?:0?[1-9]|1[0-2])$/;

/**
 * Range check on the calendar field strings. Done with regexes rather than `parseInt` so that
 * this file contains no numeric parsing at all — that keeps the "decimals never become JS
 * numbers" rule mechanically checkable by grepping for Number/parseInt/parseFloat.
 */
function isPlausible(day: string | undefined, month: string | undefined): boolean {
  if (day === undefined || month === undefined) return false;
  return DAY_RE.test(day) && MONTH_RE.test(month);
}

/** "1 Klausur" / "3 Klausuren" — trivial German plural helper for count labels. */
export function pluralize(count: number, singular: string, plural: string): string {
  return `${count} ${count === 1 ? singular : plural}`;
}
