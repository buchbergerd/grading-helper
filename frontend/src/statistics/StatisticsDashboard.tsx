import type { JSX } from "react";

import type { ExamStatistics } from "../api/client";
import { GradeDistributionChart, HistogramChart, VersuchChart } from "./charts";
import { buildStatisticsSeries, formatRate, type HistogramBarDatum } from "./series";
import type { BonusSimulationState } from "./useBonusSimulation";
import { formatDecimal, formatDecimalOrDash } from "../util/format";
import { ErrorList } from "../components/Messages";

/**
 * §9's read-only dashboard body — Kennzahlen, the bonus-points "what if" simulation box,
 * Notenverteilung, the total-points and per-exercise histograms, and the Versuch breakdown.
 * Shared verbatim by `ExamStatisticsPage` (the authenticated view, which wraps this in its own
 * breadcrumb/heading and a separate "Berichte" panel for the §10/§11 report downloads) and
 * `SharedStatisticsPage` (the §3 share-link view, which has no session and therefore no access to
 * those reports at all — they are simply never rendered here, not hidden by a prop).
 *
 * Deliberately computes nothing beyond reshaping the payload for charts: every number rendered
 * here either came straight off `ExamStatistics` or `simulation.simulatedStats` (both formatted
 * with `formatDecimal`/`formatDecimalOrDash`/`formatRate`), or is an integer count that was always
 * an int on the wire — see `src/statistics/series.ts` for the one exception (`count` -> chart bar
 * height).
 */
export function StatisticsDashboard({
  stats,
  simulation,
}: {
  stats: ExamStatistics;
  simulation: BonusSimulationState;
}): JSX.Element {
  const {
    simulationEnabled,
    bonusText,
    sliderPosition,
    simulatedStats,
    simulatedBonusCanonical,
    simulationMessages,
    setBonusText,
    onToggleSimulation,
  } = simulation;

  const series = buildStatisticsSeries(stats);

  // Kennzahlen, the grade-distribution, total-points-histogram and Versuch-breakdown sections
  // switch to the simulated payload — only the exercise histograms keep showing `stats`/`series`,
  // the real numbers, since no per-exercise entry ever depends on the exam-wide bonus. Within
  // Kennzahlen only `counts.passed`/`failed` and `rates.passing`/`failure` actually move — every
  // other count there (`registered`, `attended`, `graded`, `incomplete`, …) is decided by
  // attendance/completeness before bonus is even considered (`app/statistics.py::_classify`), so
  // reading the whole panel from the simulated payload changes nothing for those, only relabels
  // the panel while the two that do move update correctly.
  const usingSimulation = simulationEnabled && simulatedStats !== null && simulatedBonusCanonical !== null;
  const simulationSeries = simulatedStats !== null ? buildStatisticsSeries(simulatedStats) : null;
  const activeCounts = usingSimulation ? simulatedStats.counts : stats.counts;
  const activeRates = usingSimulation ? simulatedStats.rates : stats.rates;
  const activeGradeDistribution = usingSimulation
    ? simulatedStats.grade_distribution
    : stats.grade_distribution;
  const activeGradeBars = usingSimulation && simulationSeries !== null
    ? simulationSeries.gradeDistribution
    : series.gradeDistribution;
  const activeTotalPointsHistogram = usingSimulation
    ? simulatedStats.total_points_histogram
    : stats.total_points_histogram;
  const activeTotalPointsBars = usingSimulation && simulationSeries !== null
    ? simulationSeries.totalPointsHistogram
    : series.totalPointsHistogram;
  const activeThresholdBinLabel = usingSimulation && simulationSeries !== null
    ? simulationSeries.totalPointsThresholdBinLabel
    : series.totalPointsThresholdBinLabel;
  const activePassingThreshold = usingSimulation
    ? simulatedStats.passing_threshold
    : stats.passing_threshold;
  const activeVersuch = usingSimulation && simulationSeries !== null
    ? simulationSeries.versuch
    : series.versuch;
  const simulationTitleSuffix = usingSimulation
    ? ` — Simulation (${formatDecimal(simulatedBonusCanonical)} Bonuspunkte)`
    : "";

  return (
    <>
      {series.banner.visible ? (
        <div className="notice warn" role="alert" data-testid="grading-progress-banner">
          <strong>Die Klausur wird noch bearbeitet.</strong>
          <ul>
            {!series.banner.gradingConfigured ? (
              <li>
                Es ist noch kein vollständiger Notenschlüssel konfiguriert — es konnten noch für
                niemanden Noten berechnet werden.
              </li>
            ) : null}
            {series.banner.pendingCount > 0 ? (
              <li data-testid="banner-pending-count">
                {series.banner.pendingCount === 1
                  ? "1 Studierende bzw. Studierender ist"
                  : `${series.banner.pendingCount} Studierende sind`}{" "}
                noch nicht in der Notenverteilung und der Gesamtpunkte-Verteilung berücksichtigt
                (Anwesenheit oder Punkte fehlen noch).
              </li>
            ) : null}
          </ul>
          <p className="small" style={{ margin: "0.35rem 0 0" }}>
            Diese Ansicht zeigt den aktuellen Stand der Eingabe — kein Endergebnis.
          </p>
        </div>
      ) : null}

      {/* ------------------------------------------------------------------------- Kennzahlen */}
      <div className="panel">
        <h2 style={{ marginTop: 0 }}>Kennzahlen{simulationTitleSuffix}</h2>
        <div className="kpi-grid">
          <KpiCard label="Angemeldet" value={activeCounts.registered} testId="kpi-registered" />
          <KpiCard label="Anwesend" value={activeCounts.attended} testId="kpi-attended" />
          <KpiCard
            label="Nicht angetreten"
            value={activeCounts.not_attended}
            testId="kpi-not-attended"
          />
          <KpiCard
            label="Noch nicht erfasst"
            value={activeCounts.attendance_not_recorded}
            testId="kpi-attendance-not-recorded"
          />
          <KpiCard label="Bewertet" value={activeCounts.graded} testId="kpi-graded" />
          <KpiCard label="Unvollständig" value={activeCounts.incomplete} testId="kpi-incomplete" />
          {/* Only meaningful before a grading schema exists, and always 0 afterwards — shown
              conditionally so the common case isn't cluttered with a permanent zero, but never
              hidden while it is non-zero, or those students would appear nowhere at all. */}
          {activeCounts.awaiting_schema > 0 && (
            <KpiCard
              label="Ohne Notenschema"
              value={activeCounts.awaiting_schema}
              testId="kpi-awaiting-schema"
            />
          )}
        </div>
        <div className="kpi-grid" style={{ marginTop: "0.75rem" }}>
          <KpiCard
            label="Anwesenheitsquote"
            value={formatRate(activeRates.attendance)}
            testId="rate-attendance"
          />
          <KpiCard
            label="Bestehensquote"
            value={formatRate(activeRates.passing)}
            testId="rate-passing"
          />
          <KpiCard
            label="Durchfallquote"
            value={formatRate(activeRates.failure)}
            testId="rate-failure"
          />
        </div>
      </div>

      {/* ------------------------------------------------------ Simulation: Bonuspunkte (§9) */}
      <div className="panel" data-testid="bonus-simulation-panel">
        <h2 style={{ marginTop: 0 }}>Simulation: Bonuspunkte</h2>
        <label htmlFor="simulation-toggle" style={{ display: "inline", fontWeight: "normal" }}>
          <input
            id="simulation-toggle"
            type="checkbox"
            checked={simulationEnabled}
            onChange={(event) => onToggleSimulation(event.target.checked)}
            data-testid="simulation-toggle"
          />{" "}
          Was wäre, wenn ich Bonuspunkte vergebe? Notenverteilung und Gesamtpunkte-Histogramm
          unten zeigen dann die Simulation statt der aktuellen Werte.
        </label>
        {simulationEnabled ? (
          <div className="simulation-box" data-testid="simulation-box">
            <label htmlFor="simulation-bonus-input" style={{ display: "inline" }}>
              Bonuspunkte:{" "}
              <input
                id="simulation-bonus-input"
                className="narrow"
                type="text"
                inputMode="decimal"
                value={bonusText}
                onChange={(event) => setBonusText(event.target.value)}
                data-testid="simulation-bonus-input"
              />
            </label>
            <input
              type="range"
              min="0"
              max="10"
              step="0.5"
              value={sliderPosition}
              onChange={(event) => setBonusText(event.target.value)}
              aria-label="Bonuspunkte (Schieberegler, 0 bis 10)"
              data-testid="simulation-bonus-slider"
              style={{ display: "block", width: "100%", maxWidth: "24rem", margin: "0.5rem 0" }}
            />
            <ErrorList messages={simulationMessages} />
            {stats.bonus_mode === "ONLY_IF_PASSING_WITHOUT_BONUS" ? (
              <p className="muted small" data-testid="simulation-bonus-mode-note">
                Hinweis: In diesem Bonus-Modus profitieren nur Studierende, die auch ohne Bonus
                bereits bestanden hätten — ein simulierter Bonus rettet niemanden, der ohne Bonus
                durchgefallen wäre.
              </p>
            ) : null}
            {usingSimulation ? (
              <p data-testid="simulation-would-pass">
                Bei <strong>{formatDecimal(simulatedBonusCanonical)}</strong> Bonuspunkten
                würden <strong>{simulatedStats.counts.passed}</strong> von{" "}
                <strong>{simulatedStats.counts.graded}</strong> Studierenden bestehen{" "}
                <span className="muted small">
                  (aktuell {stats.counts.passed} von {stats.counts.graded})
                </span>
                .
              </p>
            ) : null}
          </div>
        ) : null}
      </div>

      {/* -------------------------------------------------------------------- Notenverteilung */}
      <div className="panel">
        <h2 style={{ marginTop: 0 }}>Notenverteilung{simulationTitleSuffix}</h2>
        <GradeDistributionChart data={activeGradeBars} />
        <p data-testid="grade-summary">
          Mittelwert: <strong>{formatDecimalOrDash(activeGradeDistribution.mean)}</strong>,
          Median: <strong>{formatDecimalOrDash(activeGradeDistribution.median)}</strong>{" "}
          <span className="muted small">
            (über {activeGradeDistribution.numeric_count}{" "}
            {activeGradeDistribution.numeric_count === 1
              ? "Studierenden mit einer numerischen Note"
              : "Studierende mit einer numerischen Note"}
            )
          </span>
        </p>
        <details className="chart-table-details" data-testid="grade-distribution-table-details">
          <summary>Werte als Tabelle anzeigen</summary>
          <table>
            <caption className="visually-hidden">Notenverteilung je Note</caption>
            <thead>
              <tr>
                <th scope="col">Note</th>
                <th scope="col" className="numeric">
                  Anzahl
                </th>
              </tr>
            </thead>
            <tbody>
              {activeGradeBars.map((entry) => (
                <tr key={entry.label} data-testid={`grade-row-${entry.label}`}>
                  <th scope="row">{entry.label}</th>
                  <td className="numeric">{entry.count}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </details>
      </div>

      {/* ------------------------------------------------------------ Histogramm Gesamtpunkte */}
      <HistogramSection
        title={`Histogramm der Gesamtpunkte${simulationTitleSuffix}`}
        maxObserved={activeTotalPointsHistogram.max_observed}
        referenceMax={activeTotalPointsHistogram.reference_max}
        includedCount={activeTotalPointsHistogram.included_count}
        bars={activeTotalPointsBars}
        thresholdBinLabel={activeThresholdBinLabel}
        passingThreshold={activePassingThreshold}
        testIdPrefix="total-points-histogram"
      />

      {/* --------------------------------------------------------------------- pro Aufgabe */}
      <div className="exercise-histogram-grid">
        {series.exerciseHistograms.map((histogram, index) => {
          const source = stats.exercise_histograms[index];
          return (
            <HistogramSection
              key={histogram.title}
              title={histogram.title}
              maxObserved={source?.max_observed ?? null}
              referenceMax={source?.reference_max ?? "0"}
              includedCount={source?.included_count ?? 0}
              bars={histogram.bars}
              testIdPrefix={`exercise-histogram-${index}`}
            />
          );
        })}
      </div>

      {/* --------------------------------------------------------- Bestehensquote nach Versuch */}
      <div className="panel">
        <h2 style={{ marginTop: 0 }}>Bestehensquote nach Versuch{simulationTitleSuffix}</h2>
        {activeVersuch.length === 0 ? (
          <p className="muted">Keine Daten vorhanden.</p>
        ) : (
          <>
            <VersuchChart data={activeVersuch} />
            <details className="chart-table-details" data-testid="versuch-table-details">
              <summary>Werte als Tabelle anzeigen</summary>
              <table>
                <caption className="visually-hidden">Bestehensquote nach Versuch</caption>
                <thead>
                  <tr>
                    <th scope="col">Versuch</th>
                    <th scope="col" className="numeric">
                      Bestanden
                    </th>
                    <th scope="col" className="numeric">
                      Nicht bestanden
                    </th>
                    <th scope="col">Durchfallquote</th>
                  </tr>
                </thead>
                <tbody>
                  {activeVersuch.map((group) => (
                    <tr key={group.versuch} data-testid={`versuch-row-${group.versuch}`}>
                      <th scope="row">{group.label}</th>
                      <td className="numeric">{group.passed}</td>
                      <td className="numeric">{group.failed}</td>
                      <td>{formatRate(group.failureRate)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </details>
          </>
        )}
      </div>
    </>
  );
}

function KpiCard({
  label,
  value,
  testId,
}: {
  label: string;
  value: number | string;
  testId: string;
}): JSX.Element {
  return (
    <div className="kpi-card" data-testid={testId}>
      <span className="kpi-label">{label}</span>
      <span className="kpi-value">{value}</span>
    </div>
  );
}

function HistogramSection({
  title,
  maxObserved,
  referenceMax,
  includedCount,
  bars,
  thresholdBinLabel = null,
  passingThreshold = null,
  testIdPrefix,
}: {
  title: string;
  maxObserved: string | null;
  /** The exam's total max points, or the exercise's `max_points` — shown alongside
   * `maxObserved` as "<value> / <referenceMax>". */
  referenceMax: string;
  includedCount: number;
  bars: HistogramBarDatum[];
  /** Only set for the total-points histogram — see `series.ts::thresholdBinLabel`. */
  thresholdBinLabel?: string | null;
  /** The §9 passing threshold itself, shown as text alongside the chart's dashed marker line so
   * the exact value survives even where the chart isn't rendered (print, screen readers). */
  passingThreshold?: string | null;
  testIdPrefix: string;
}): JSX.Element {
  return (
    <div className="panel">
      <h2 style={{ marginTop: 0 }}>{title}</h2>
      <p className="muted small" data-testid={`${testIdPrefix}-meta`}>
        {includedCount === 1
          ? "1 Studierende bzw. Studierender berücksichtigt"
          : `${includedCount} Studierende berücksichtigt`}
        {" — "}höchster beobachteter Wert: {formatDecimalOrDash(maxObserved)} /{" "}
        {formatDecimal(referenceMax)}
        {passingThreshold !== null && (
          <>
            {" — "}Bestehensgrenze: {formatDecimalOrDash(passingThreshold)} Punkte
          </>
        )}
      </p>
      {bars.length === 0 ? (
        <p className="muted">Noch keine Daten.</p>
      ) : (
        <>
          <HistogramChart data={bars} thresholdBinLabel={thresholdBinLabel} />
          <details className="chart-table-details" data-testid={`${testIdPrefix}-table-details`}>
            <summary>Werte als Tabelle anzeigen</summary>
            <table>
              <caption className="visually-hidden">{title}</caption>
              <thead>
                <tr>
                  <th scope="col">Bereich</th>
                  <th scope="col" className="numeric">
                    Anzahl
                  </th>
                </tr>
              </thead>
              <tbody>
                {bars.map((bin, index) => (
                  <tr key={`${bin.label}-${index}`} data-testid={`${testIdPrefix}-row-${index}`}>
                    <th scope="row">{bin.label}</th>
                    <td className="numeric">{bin.count}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </details>
        </>
      )}
    </div>
  );
}
