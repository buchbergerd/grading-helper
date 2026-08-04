import { useCallback, useEffect, useRef, useState, type JSX } from "react";
import { Link, useParams } from "react-router";

import {
  downloadExaminationOfficeExcel,
  downloadExaminationOfficePdf,
  downloadInternalReport,
  downloadStudentResultsExcel,
  downloadStudentResultsPdf,
  errorMessages,
  getCompleteness,
  getExam,
  getExamStatistics,
  type CompletenessResult,
  type DownloadedFile,
  type ExamDetail,
  type ExamStatistics,
} from "../api/client";
import { GradeDistributionChart, HistogramChart, VersuchChart } from "../statistics/charts";
import {
  buildStatisticsSeries,
  formatRate,
  type HistogramBarDatum,
} from "../statistics/series";
import { BONUS_SIMULATION_DEBOUNCE_MS, sliderPositionFor } from "../statistics/bonusSimulation";
import { BackButton } from "../components/BackButton";
import { ErrorList, SuccessNotice } from "../components/Messages";
import { formatDateOrDash, formatDecimal, formatDecimalOrDash, parseDecimalInput } from "../util/format";
import { parseRouteId } from "../util/id";

/** One of the four §10/§11 report downloads offered once the exam is export-ready. */
type ReportDownloadKind =
  | "examination-office-pdf"
  | "examination-office-excel"
  | "student-results-pdf"
  | "student-results-excel";

/**
 * §9's live dashboard. Deliberately computes nothing: every number rendered here either came
 * straight off `ExamStatistics` (a decimal string, formatted with `formatDecimal`/
 * `formatDecimalOrDash`/`formatRate`) or is an integer `count`/`numerator`/`denominator` that
 * was always an int on the wire — never a value derived from a Decimal by this file. See
 * `src/statistics/series.ts` for why that split is safe and where the one exception
 * (`count` -> chart bar height) lives.
 */
export default function ExamStatisticsPage(): JSX.Element {
  const params = useParams();
  const examId = parseRouteId(params["examId"]);

  const [exam, setExam] = useState<ExamDetail | null>(null);
  const [stats, setStats] = useState<ExamStatistics | null>(null);
  const [loading, setLoading] = useState(true);
  const [messages, setMessages] = useState<string[]>([]);

  const [downloading, setDownloading] = useState(false);
  const [downloadMessages, setDownloadMessages] = useState<string[]>([]);

  const [completeness, setCompleteness] = useState<CompletenessResult | null>(null);
  const [completenessMessages, setCompletenessMessages] = useState<string[]>([]);

  // §10/§11 report downloads. One key tracks which of the four buttons is in flight (rather than
  // a plain boolean) so its own label can say "Wird erstellt …" while the other three stay in
  // their normal state.
  const [downloadingReport, setDownloadingReport] = useState<ReportDownloadKind | null>(null);
  const [reportDownloadMessages, setReportDownloadMessages] = useState<string[]>([]);

  // The "what if" bonus-points simulation box. `bonusText` is the raw input-field text — the
  // task's explicit requirement that this field is never bound-checked, so it is *not* clamped to
  // the slider's 0-10 range before being sent. `simulatedStats` is a second, independent
  // `ExamStatistics` payload (`?bonus_points_override=...`, see `api/client.ts`) that only the
  // grade-distribution, total-points-histogram and Versuch-breakdown sections read from — every
  // other section on this page (KPIs, exercise histograms) keeps reading the real `stats` so
  // those never silently become hypothetical.
  const [simulationEnabled, setSimulationEnabled] = useState(false);
  const [bonusText, setBonusText] = useState("0");
  // The slider's own displayed position — kept as separate state, not derived fresh from
  // `bonusText` every render, so that typing an off-grid or out-of-range value (task requirement:
  // the field is never bound-checked) leaves the thumb where it last was instead of jumping to a
  // fallback. Only updated when `bonusText` actually lands exactly on one of the slider's stops.
  const [sliderPosition, setSliderPosition] = useState("0");
  const [simulatedStats, setSimulatedStats] = useState<ExamStatistics | null>(null);
  // The canonical bonus value that actually produced `simulatedStats` — set together with it, in
  // the same state update. Rendering labels from this instead of live-parsing `bonusText` avoids
  // a heading that names a value the chart doesn't reflect yet: while `bonusText` is transiently
  // unparseable (e.g. mid-typing "1,"), `simulatedStats` still holds the previous good payload,
  // and the label must say what that payload was actually computed for, not what's in the field.
  const [simulatedBonusCanonical, setSimulatedBonusCanonical] = useState<string | null>(null);
  const [simulationMessages, setSimulationMessages] = useState<string[]>([]);
  // Bumped on every keystroke/slider tick that starts a new debounced fetch; a resolving request
  // only applies its result if it is still the most recent one requested. Debouncing alone only
  // protects against firing too many requests, not against two in-flight ones resolving out of
  // order (a slow response to an earlier value landing after a fast response to a later one) —
  // this guards that too.
  const simulationRequestId = useRef(0);

  const reload = useCallback(async () => {
    if (examId === null) {
      setMessages(["Ungültige Adresse."]);
      setLoading(false);
      return;
    }
    setLoading(true);
    try {
      const [examDetail, statistics] = await Promise.all([
        getExam(examId),
        getExamStatistics(examId),
      ]);
      setExam(examDetail);
      setStats(statistics);
      setMessages([]);
    } catch (error) {
      setMessages(errorMessages(error));
    } finally {
      setLoading(false);
    }
  }, [examId]);

  const reloadCompleteness = useCallback(async () => {
    if (examId === null) return;
    try {
      setCompleteness(await getCompleteness(examId));
      setCompletenessMessages([]);
    } catch (error) {
      setCompletenessMessages(errorMessages(error));
    }
  }, [examId]);

  useEffect(() => {
    void reload();
    void reloadCompleteness();
  }, [reload, reloadCompleteness]);

  useEffect(() => {
    const position = sliderPositionFor(bonusText);
    if (position !== null) setSliderPosition(position);
  }, [bonusText]);

  useEffect(() => {
    if (examId === null || !simulationEnabled) {
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
    const requestId = ++simulationRequestId.current;
    const timer = window.setTimeout(() => {
      void (async () => {
        try {
          const result = await getExamStatistics(examId, canonical);
          if (simulationRequestId.current === requestId) {
            setSimulatedStats(result);
            setSimulatedBonusCanonical(canonical);
          }
        } catch (error) {
          if (simulationRequestId.current === requestId) setSimulationMessages(errorMessages(error));
        }
      })();
    }, BONUS_SIMULATION_DEBOUNCE_MS);
    return () => window.clearTimeout(timer);
  }, [examId, simulationEnabled, bonusText]);

  function onToggleSimulation(checked: boolean): void {
    setSimulationEnabled(checked);
    if (checked) {
      setBonusText(exam?.bonus_points ?? "0");
    } else {
      setSimulatedStats(null);
      setSimulatedBonusCanonical(null);
      setSimulationMessages([]);
    }
  }

  /**
   * Shared by all four §10/§11 report buttons: blob -> `URL.createObjectURL` -> a temporary
   * `<a download>` click -> revoke, same pattern as `onDownloadPdf` below. A stale-UI `409` (the
   * exam stopped being export-ready between this page loading and the click) surfaces through the
   * same `errorMessages`/`ErrorList` path as every other error on this page rather than a bespoke
   * one.
   */
  async function downloadReport(
    kind: ReportDownloadKind,
    fetcher: () => Promise<DownloadedFile>,
  ): Promise<void> {
    setDownloadingReport(kind);
    setReportDownloadMessages([]);
    try {
      const { blob, filename } = await fetcher();
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = filename;
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      URL.revokeObjectURL(url);
    } catch (error) {
      setReportDownloadMessages(errorMessages(error));
    } finally {
      setDownloadingReport(null);
    }
  }

  async function onDownloadPdf(): Promise<void> {
    if (examId === null) return;
    setDownloading(true);
    setDownloadMessages([]);
    try {
      const { blob, filename } = await downloadInternalReport(examId);
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = filename;
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      URL.revokeObjectURL(url);
    } catch (error) {
      setDownloadMessages(errorMessages(error));
    } finally {
      setDownloading(false);
    }
  }

  if (loading) return <p className="muted">Wird geladen …</p>;

  if (examId === null || stats === null) {
    return (
      <section>
        <ErrorList messages={messages} />
        <Link to="/">Zur Vorlesungsübersicht</Link>
      </section>
    );
  }

  const series = buildStatisticsSeries(stats);

  // Kennzahlen, the grade-distribution, total-points-histogram and Versuch-breakdown sections
  // switch to the simulated payload — only the exercise histograms keep showing `stats`/`series`,
  // the real numbers, since no per-exercise entry ever depends on the exam-wide bonus. Within
  // Kennzahlen only `counts.passed`/`failed` and `rates.passing`/`failure` actually move — every
  // other count there (`registered`, `attended`, `graded`, `incomplete`, …) is decided by
  // attendance/completeness before bonus is even considered (`app/statistics.py::_classify`), so
  // reading the whole panel from the simulated payload changes nothing for those, only relabels
  // the panel while the two that do move update correctly.
  const usingSimulation =
    simulationEnabled && simulatedStats !== null && simulatedBonusCanonical !== null;
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

  // The four §10/§11 buttons stay visible even when the exam isn't export-ready yet — greyed
  // out rather than hidden, so instructors always know the reports exist and what's blocking them.
  const reportButtonsDisabled =
    completeness === null ||
    !completeness.is_complete ||
    !stats.grading_configured ||
    downloadingReport !== null;
  const reportButtonsDisabledReason =
    completeness !== null && !completeness.is_complete
      ? "Nicht alle Daten sind vollständig."
      : !stats.grading_configured
        ? "Der Notenschlüssel ist noch nicht vollständig konfiguriert."
        : undefined;

  return (
    <section>
      <div className="breadcrumb-row">
        <BackButton to={exam !== null ? `/klausuren/${exam.id}` : null} />
        <p className="breadcrumb">
          <Link to="/">Vorlesungen</Link>
          {exam !== null ? (
            <>
              {" "}
              / <Link to={`/vorlesungen/${exam.lecture_id}`}>{exam.lecture_name}</Link> /{" "}
              <Link to={`/klausuren/${exam.id}`}>
                {exam.semester}, {exam.termin}
              </Link>{" "}
              / Statistik
            </>
          ) : null}
        </p>
      </div>
      <h1>Interner Bericht — {stats.lecture_name}</h1>
      <p className="muted small">
        {stats.semester}, {stats.termin}
        {stats.exam_date !== null ? ` — Klausurdatum ${formatDateOrDash(stats.exam_date)}` : ""}
      </p>

      <ErrorList messages={messages} />

      <div className="panel">
        <h2 style={{ marginTop: 0 }}>Berichte</h2>
        <div className="button-row">
          <button type="button" onClick={() => void onDownloadPdf()} disabled={downloading}>
            {downloading ? "Wird erstellt …" : "Internen Bericht herunterladen"}
          </button>
        </div>
        <ErrorList messages={downloadMessages} />

        <hr />

        <div data-testid="completeness-errors">
          <ErrorList messages={completenessMessages} />
        </div>
        {completeness === null ? (
          <p className="muted">Wird geladen …</p>
        ) : (
          <>
            {completeness.is_complete ? (
              <SuccessNotice>
                Alle Daten sind vollständig — Offizielle Berichte können erzeugt werden.
              </SuccessNotice>
            ) : (
              <p className="muted small" data-testid="completeness-incomplete-hint">
                <strong>{completeness.incomplete_count}</strong>{" "}
                {completeness.incomplete_count === 1
                  ? "Studierende bzw. Studierender ist"
                  : "Studierende sind"}{" "}
                noch unvollständig — Details dazu auf der{" "}
                <Link to={`/klausuren/${examId}/punkte`}>Punkte-Seite</Link>.
              </p>
            )}
            {!stats.grading_configured ? (
              <p className="muted small" data-testid="schema-not-configured-hint">
                Der Notenschlüssel ist noch nicht vollständig konfiguriert.
              </p>
            ) : null}
            <div data-testid="report-download-errors">
              <ErrorList messages={reportDownloadMessages} />
            </div>
            <div className="button-row">
              <button
                type="button"
                data-testid="download-examination-office-pdf"
                disabled={reportButtonsDisabled}
                title={reportButtonsDisabledReason}
                onClick={() =>
                  void downloadReport("examination-office-pdf", () =>
                    downloadExaminationOfficePdf(examId),
                  )
                }
              >
                {downloadingReport === "examination-office-pdf"
                  ? "Wird erstellt …"
                  : "Prüfungsamt-Bericht als PDF herunterladen"}
              </button>
              <button
                type="button"
                data-testid="download-examination-office-excel"
                disabled={reportButtonsDisabled}
                title={reportButtonsDisabledReason}
                onClick={() =>
                  void downloadReport("examination-office-excel", () =>
                    downloadExaminationOfficeExcel(examId),
                  )
                }
              >
                {downloadingReport === "examination-office-excel"
                  ? "Wird erstellt …"
                  : "Prüfungsamt-Bericht als Excel herunterladen"}
              </button>
              <button
                type="button"
                data-testid="download-student-results-pdf"
                disabled={reportButtonsDisabled}
                title={reportButtonsDisabledReason}
                onClick={() =>
                  void downloadReport("student-results-pdf", () =>
                    downloadStudentResultsPdf(examId),
                  )
                }
              >
                {downloadingReport === "student-results-pdf"
                  ? "Wird erstellt …"
                  : "Notenliste als PDF herunterladen"}
              </button>
              <button
                type="button"
                data-testid="download-student-results-excel"
                disabled={reportButtonsDisabled}
                title={reportButtonsDisabledReason}
                onClick={() =>
                  void downloadReport("student-results-excel", () =>
                    downloadStudentResultsExcel(examId),
                  )
                }
              >
                {downloadingReport === "student-results-excel"
                  ? "Wird erstellt …"
                  : "Notenliste als Excel herunterladen"}
              </button>
            </div>
          </>
        )}
      </div>

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
    </section>
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
  includedCount,
  bars,
  thresholdBinLabel = null,
  passingThreshold = null,
  testIdPrefix,
}: {
  title: string;
  maxObserved: string | null;
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
        {" — "}höchster beobachteter Wert: {formatDecimalOrDash(maxObserved)}
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
