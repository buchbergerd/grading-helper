/**
 * German (DIN 5007-1) name collation, mirroring `app/collation.py::german_sort_key` value-for-
 * value (§6): umlauts/accents fold to their base letter (ö -> o, é -> e), ß folds to "ss", case
 * is ignored. Exists so a client-side re-sort (e.g. PointsEntryPage's on-page ordering control)
 * agrees with the server-rendered attendance list/reports instead of drifting to a plain
 * codepoint sort, which would strand "Öztürk" after "Zimmermann".
 *
 * This is a client-side convenience sort only — the authoritative order for anything printed
 * (PDF/Excel reports, the attendance list) is still computed server-side by the Python module
 * this mirrors.
 */

const PRE_NFD_EXPANSIONS: Record<string, string> = {
  ß: "ss",
  ẞ: "SS",
  æ: "ae",
  Æ: "AE",
  œ: "oe",
  Œ: "OE",
  ø: "o",
  Ø: "O",
  đ: "d",
  Đ: "D",
  ð: "d",
  Ð: "D",
  ł: "l",
  Ł: "L",
  þ: "th",
  Þ: "Th",
  ħ: "h",
  Ħ: "H",
  ı: "i",
};

function expand(value: string): string {
  let result = "";
  for (const char of value) {
    result += PRE_NFD_EXPANSIONS[char] ?? char;
  }
  return result;
}

/** `[primary, original]` — `original` is a deterministic tiebreaker, same as the backend, so
 * names differing only in diacritics ("Muller" vs "Müller") don't compare equal. */
export function germanSortKey(value: string): [string, string] {
  const decomposed = expand(value).normalize("NFD");
  const stripped = decomposed.replace(/\p{Mn}/gu, "");
  return [stripped.toLowerCase(), value];
}

export function compareGerman(a: string, b: string): number {
  const [primaryA, originalA] = germanSortKey(a);
  const [primaryB, originalB] = germanSortKey(b);
  if (primaryA !== primaryB) return primaryA < primaryB ? -1 : 1;
  if (originalA !== originalB) return originalA < originalB ? -1 : 1;
  return 0;
}
