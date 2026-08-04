/**
 * Pure helpers for the §9 dashboard's "what if" bonus-points simulation box on
 * `ExamStatisticsPage` — the checkbox that lets an instructor preview
 * `GET .../statistics?bonus_points_override=<value>` (`app/api/statistics.py`) without touching
 * the real exam.
 *
 * Kept string-only, like `util/format.ts`: the simulated bonus value only ever needs parsing
 * (`parseDecimalInput`) and a slider position, never arithmetic, so there is no reason to let it
 * touch the JS `number` type either — see that file's own docstring for why a decimal never
 * becomes a `Number()`/`parseFloat` argument in this codebase.
 */

import { parseDecimalInput } from "../util/format";

/**
 * The slider's fixed stops: 0 to 10 in 0.5 steps, as canonical dot-decimal strings — written out
 * literally rather than generated, so this list is itself the spec, not the output of arithmetic
 * that would need checking.
 */
export const BONUS_SLIDER_STEPS: readonly string[] = [
  "0",
  "0.5",
  "1",
  "1.5",
  "2",
  "2.5",
  "3",
  "3.5",
  "4",
  "4.5",
  "5",
  "5.5",
  "6",
  "6.5",
  "7",
  "7.5",
  "8",
  "8.5",
  "9",
  "9.5",
  "10",
];

/** Debounce for the simulated-statistics fetch, milliseconds. */
export const BONUS_SIMULATION_DEBOUNCE_MS = 300;

/**
 * "5.00" -> "5", "1.50" -> "1.5", "0" -> "0". Pure string surgery (no `Number()`/`parseFloat`): a
 * canonical decimal's trailing fractional zeros are trimmed so it can be compared against
 * `BONUS_SLIDER_STEPS`'s plain forms.
 */
export function trimTrailingZeros(canonical: string): string {
  if (!canonical.includes(".")) return canonical;
  return canonical.replace(/0+$/, "").replace(/\.$/, "");
}

/**
 * The slider position matching the bonus-points text field's current content, or `null` if it
 * doesn't parse to a canonical decimal that is exactly one of the slider's stops.
 *
 * `null` covers both an unparseable value and one that's out of the slider's 0-10/step-0.5 grid
 * (e.g. "25", or "3.25") — the task's explicit requirement that the input field is never
 * bound-checked and "overrides" the slider: the caller keeps the slider wherever it last was
 * rather than clamping or guessing a nearest stop.
 */
export function sliderPositionFor(text: string): string | null {
  const canonical = parseDecimalInput(text);
  if (canonical === null) return null;
  const trimmed = trimTrailingZeros(canonical);
  return BONUS_SLIDER_STEPS.includes(trimmed) ? trimmed : null;
}
