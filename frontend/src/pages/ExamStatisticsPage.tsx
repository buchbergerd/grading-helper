import { useCallback, useEffect, useState, type JSX } from "react";
import { Link, useParams } from "react-router-dom";

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
import { BackButton } from "../components/BackButton";
import { ErrorList, SuccessNotice } from "../components/Messages";
import { formatDateOrDash, formatDecimalOrDash } from "../util/format";
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
        <h2 style={{ marginTop: 0 }}>Kennzahlen</h2>
        <div className="kpi-grid">
          <KpiCard label="Angemeldet" value={stats.counts.registered} testId="kpi-registered" />
          <KpiCard label="Anwesend" value={stats.counts.attended} testId="kpi-attended" />
          <KpiCard
            label="Nicht angetreten"
            value={stats.counts.not_attended}
            testId="kpi-not-attended"
          />
          <KpiCard
            label="Noch nicht erfasst"
            value={stats.counts.attendance_not_recorded}
            testId="kpi-attendance-not-recorded"
          />
          <KpiCard label="Bewertet" value={stats.counts.graded} testId="kpi-graded" />
          <KpiCard label="Unvollständig" value={stats.counts.incomplete} testId="kpi-incomplete" />
          {/* Only meaningful before a grading schema exists, and always 0 afterwards — shown
              conditionally so the common case isn't cluttered with a permanent zero, but never
              hidden while it is non-zero, or those students would appear nowhere at all. */}
          {stats.counts.awaiting_schema > 0 && (
            <KpiCard
              label="Ohne Notenschema"
              value={stats.counts.awaiting_schema}
              testId="kpi-awaiting-schema"
            />
          )}
        </div>
        <div className="kpi-grid" style={{ marginTop: "0.75rem" }}>
          <KpiCard
            label="Anwesenheitsquote"
            value={formatRate(stats.rates.attendance)}
            testId="rate-attendance"
          />
          <KpiCard
            label="Bestehensquote"
            value={formatRate(stats.rates.passing)}
            testId="rate-passing"
          />
          <KpiCard
            label="Durchfallquote"
            value={formatRate(stats.rates.failure)}
            testId="rate-failure"
          />
        </div>
      </div>

      {/* -------------------------------------------------------------------- Notenverteilung */}
      <div className="panel">
        <h2 style={{ marginTop: 0 }}>Notenverteilung</h2>
        <GradeDistributionChart data={series.gradeDistribution} />
        <p data-testid="grade-summary">
          Mittelwert: <strong>{formatDecimalOrDash(stats.grade_distribution.mean)}</strong>,
          Median: <strong>{formatDecimalOrDash(stats.grade_distribution.median)}</strong>{" "}
          <span className="muted small">
            (über {stats.grade_distribution.numeric_count}{" "}
            {stats.grade_distribution.numeric_count === 1
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
              {series.gradeDistribution.map((entry) => (
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
        title="Histogramm der Gesamtpunkte"
        maxObserved={stats.total_points_histogram.max_observed}
        includedCount={stats.total_points_histogram.included_count}
        bars={series.totalPointsHistogram}
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
        <h2 style={{ marginTop: 0 }}>Bestehensquote nach Versuch</h2>
        {series.versuch.length === 0 ? (
          <p className="muted">Keine Daten vorhanden.</p>
        ) : (
          <>
            <VersuchChart data={series.versuch} />
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
                  {series.versuch.map((group) => (
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
  testIdPrefix,
}: {
  title: string;
  maxObserved: string | null;
  includedCount: number;
  bars: HistogramBarDatum[];
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
      </p>
      {bars.length === 0 ? (
        <p className="muted">Noch keine Daten.</p>
      ) : (
        <>
          <HistogramChart data={bars} />
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
