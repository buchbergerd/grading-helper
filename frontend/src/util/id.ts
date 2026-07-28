/**
 * The one place in this app where a string is turned into a JS number.
 *
 * Entity ids are small integers (§4), not decimals — they are never summed, compared against a
 * threshold or shown to a user with a decimal separator, so a double represents them exactly
 * and the §7.0 ban on binary floats does not apply. Points, percentages and thresholds are a
 * different matter entirely and must never come near this function; they stay strings and are
 * computed on only in `src/grading/preview.ts` on an explicit bigint scale.
 */
export function parseRouteId(raw: string | undefined): number | null {
  if (raw === undefined) return null;
  if (!/^\d+$/.test(raw)) return null;
  const id = Number(raw);
  return Number.isSafeInteger(id) ? id : null;
}

/**
 * `versuch` (attempt number, §4) is a small positive integer, not a decimal — the §7.0 ban on
 * `Number()`/`parseInt` applies to points/percentages/thresholds, not to this field. Kept here
 * rather than inline in a page so the "the one place a string becomes a number" grep check in
 * `frontend/README.md` still finds only this file.
 */
export function parsePositiveInteger(raw: string): number | null {
  const trimmed = raw.trim();
  if (!/^\d+$/.test(trimmed)) return null;
  const value = Number(trimmed);
  return Number.isSafeInteger(value) && value >= 1 ? value : null;
}
