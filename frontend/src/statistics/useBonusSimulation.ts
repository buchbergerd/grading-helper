import { useCallback, useEffect, useRef, useState } from "react";

import { errorMessages, type ExamStatistics } from "../api/client";
import { parseDecimalInput } from "../util/format";
import { BONUS_SIMULATION_DEBOUNCE_MS, sliderPositionFor } from "./bonusSimulation";

/**
 * The §9 dashboard's "what if" bonus-points simulation box, factored out of `ExamStatisticsPage`
 * so `SharedStatisticsPage` (the §3 share-link view) gets the identical debounced-fetch/slider
 * behaviour without duplicating it. `fetchStats` is the caller's own request — the authenticated
 * page passes `getExamStatistics(examId, ...)`, the public page passes
 * `getSharedStatistics(token, ...)` — this hook does not know or care which.
 */
export interface BonusSimulationState {
  simulationEnabled: boolean;
  /** The raw input-field text — never bound-checked (task requirement), so not clamped to the
   * slider's 0-10 range before being sent. */
  bonusText: string;
  /** The slider's own displayed position, only updated when `bonusText` lands exactly on one of
   * its stops — see `bonusSimulation.ts::sliderPositionFor`. */
  sliderPosition: string;
  /** A second, independent `ExamStatistics` payload for the "what if" sections; `null` until the
   * first successful simulated fetch. */
  simulatedStats: ExamStatistics | null;
  /** The canonical bonus value that actually produced `simulatedStats` — set together with it, so
   * a label built from this always matches the payload it describes, unlike live-parsing
   * `bonusText` (which can be transiently unparseable mid-typing). */
  simulatedBonusCanonical: string | null;
  simulationMessages: string[];
  setBonusText: (text: string) => void;
  onToggleSimulation: (checked: boolean) => void;
}

export function useBonusSimulation(
  fetchStats: (bonusPointsOverride?: string) => Promise<ExamStatistics>,
  /** What the bonus field resets to when the checkbox is (re)enabled — e.g. the exam's real
   * `bonus_points`. Read fresh at toggle time via a ref, not captured once at mount, so it still
   * reflects a value that only became available after this hook was first created. */
  resetBonusText: string,
): BonusSimulationState {
  const [simulationEnabled, setSimulationEnabled] = useState(false);
  const [bonusText, setBonusText] = useState("0");
  const [sliderPosition, setSliderPosition] = useState("0");
  const [simulatedStats, setSimulatedStats] = useState<ExamStatistics | null>(null);
  const [simulatedBonusCanonical, setSimulatedBonusCanonical] = useState<string | null>(null);
  const [simulationMessages, setSimulationMessages] = useState<string[]>([]);
  // Bumped on every keystroke/slider tick that starts a new debounced fetch; a resolving request
  // only applies its result if it is still the most recent one requested — guards against two
  // in-flight requests resolving out of order, which debouncing alone does not prevent.
  const requestId = useRef(0);
  const resetBonusTextRef = useRef(resetBonusText);

  useEffect(() => {
    resetBonusTextRef.current = resetBonusText;
  }, [resetBonusText]);

  useEffect(() => {
    const position = sliderPositionFor(bonusText);
    if (position !== null) setSliderPosition(position);
  }, [bonusText]);

  useEffect(() => {
    if (!simulationEnabled) {
      setSimulatedStats(null);
      setSimulatedBonusCanonical(null);
      setSimulationMessages([]);
      return;
    }
    const canonical = parseDecimalInput(bonusText);
    if (canonical === null) {
      setSimulationMessages(['Ungültige Zahl — bitte z. B. "1,5" eingeben.']);
      return;
    }
    setSimulationMessages([]);
    const id = ++requestId.current;
    const timer = window.setTimeout(() => {
      void (async () => {
        try {
          const result = await fetchStats(canonical);
          if (requestId.current === id) {
            setSimulatedStats(result);
            setSimulatedBonusCanonical(canonical);
          }
        } catch (error) {
          if (requestId.current === id) setSimulationMessages(errorMessages(error));
        }
      })();
    }, BONUS_SIMULATION_DEBOUNCE_MS);
    return () => window.clearTimeout(timer);
  }, [simulationEnabled, bonusText, fetchStats]);

  const onToggleSimulation = useCallback((checked: boolean) => {
    setSimulationEnabled(checked);
    if (checked) {
      setBonusText(resetBonusTextRef.current);
    } else {
      setSimulatedStats(null);
      setSimulatedBonusCanonical(null);
      setSimulationMessages([]);
    }
  }, []);

  return {
    simulationEnabled,
    bonusText,
    sliderPosition,
    simulatedStats,
    simulatedBonusCanonical,
    simulationMessages,
    setBonusText,
    onToggleSimulation,
  };
}
