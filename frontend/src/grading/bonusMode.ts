import type { BonusMode } from "../api/client";

/**
 * The two bonus modes of SPECIFICATION.md §7.3, with the German explanation shown next to each
 * radio button. The wording of ONLY_IF_PASSING_WITHOUT_BONUS deliberately spells out that the
 * check runs against `raw_total` *before* the bonus is added — the mode is not "cap the grade
 * at pass", and getting that backwards silently turns failed exams into passes.
 */
export const BONUS_MODE_OPTIONS: readonly {
  value: BonusMode;
  label: string;
  explanation: string;
}[] = [
  {
    value: "ALWAYS",
    label: "Bonuspunkte zählen immer",
    explanation:
      "Die Bonuspunkte werden immer zur Punktsumme addiert (ohne Obergrenze) und können damit " +
      "auch zum Bestehen der Klausur führen.",
  },
  {
    value: "ONLY_IF_PASSING_WITHOUT_BONUS",
    label: "Bonuspunkte nur bei Bestehen ohne Bonus",
    explanation:
      "Die Bonuspunkte werden nur angerechnet, wenn die Punktsumme ohne Bonus bereits die " +
      "Schwelle für die Note 4,0 erreicht. Ist das nicht der Fall, bleiben die Bonuspunkte " +
      "unberücksichtigt — Bonuspunkte können die Note verbessern, aber nicht zum Bestehen führen.",
  },
];
