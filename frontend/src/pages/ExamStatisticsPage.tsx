import { useCallback, useEffect, useState, type JSX } from "react";
import { Link, useParams } from "react-router";

import {
  createShareLink,
  downloadExaminationOfficeExcel,
  downloadExaminationOfficePdf,
  downloadInternalReport,
  downloadStudentResultsExcel,
  downloadStudentResultsPdf,
  errorMessages,
  getCompleteness,
  getExam,
  getExamStatistics,
  revokeShareLink,
  type CompletenessResult,
  type DownloadedFile,
  type ExamDetail,
  type ExamStatistics,
} from "../api/client";
import { StatisticsDashboard } from "../statistics/StatisticsDashboard";
import { useBonusSimulation } from "../statistics/useBonusSimulation";
import { BackButton } from "../components/BackButton";
import { ErrorList, SuccessNotice } from "../components/Messages";
import { parseRouteId } from "../util/id";

/** One of the four §10/§11 report downloads offered once the exam is export-ready. */
type ReportDownloadKind =
  | "examination-office-pdf"
  | "examination-office-excel"
  | "student-results-pdf"
  | "student-results-excel";

/**
 * §9's live dashboard: this page owns the breadcrumb/heading, the "Berichte" panel (§9 PDF plus
 * the §10/§11 downloads, all owner-only — never reachable through the §3 share link), the §8.1
 * completeness panel, and share-link management. The dashboard itself (Kennzahlen, the bonus
 * simulation box, Notenverteilung, histograms, Versuch) is `StatisticsDashboard`, shared with the
 * unauthenticated `SharedStatisticsPage`.
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

  // §3's share-link panel. `sharePending` covers both the create/regenerate and the revoke
  // request — never both hit the button row at once, so one flag is enough.
  const [sharePending, setSharePending] = useState(false);
  const [shareMessages, setShareMessages] = useState<string[]>([]);
  const [shareNotice, setShareNotice] = useState<string | null>(null);

  const fetchStats = useCallback(
    (bonusPointsOverride?: string) => {
      if (examId === null) throw new Error("unreachable: dashboard only renders with a valid examId");
      return getExamStatistics(examId, bonusPointsOverride);
    },
    [examId],
  );
  const simulation = useBonusSimulation(fetchStats, exam?.bonus_points ?? "0");

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

  function shareLinkUrl(token: string): string {
    return `${window.location.origin}/geteilt/statistik/${encodeURIComponent(token)}`;
  }

  async function onCreateOrRegenerateShareLink(): Promise<void> {
    if (examId === null) return;
    setSharePending(true);
    setShareMessages([]);
    setShareNotice(null);
    try {
      const updated = await createShareLink(examId);
      setExam(updated);
      setShareNotice("Der Link wurde erstellt.");
    } catch (error) {
      setShareMessages(errorMessages(error));
    } finally {
      setSharePending(false);
    }
  }

  async function onCopyShareLink(token: string): Promise<void> {
    setShareNotice(null);
    try {
      await navigator.clipboard.writeText(shareLinkUrl(token));
      setShareNotice("Der Link wurde in die Zwischenablage kopiert.");
    } catch {
      setShareMessages(["Der Link konnte nicht kopiert werden. Bitte manuell weitergeben."]);
    }
  }

  async function onRevokeShareLink(): Promise<void> {
    if (examId === null) return;
    setSharePending(true);
    setShareMessages([]);
    setShareNotice(null);
    try {
      await revokeShareLink(examId);
      setExam((current) => (current === null ? current : { ...current, share_token: null }));
      setShareNotice("Der Link wurde widerrufen.");
    } catch (error) {
      setShareMessages(errorMessages(error));
    } finally {
      setSharePending(false);
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
        {/* `stats.exam_date` is already `DD.MM.YYYY` off the wire (§14 #6) — unlike
            `ExamDetail.exam_date` (ISO), it must not go through formatDateOrDash again. */}
        {stats.exam_date !== null ? ` — Klausurdatum ${stats.exam_date}` : ""}
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

      {/* --------------------------------------------------------------------- §3 Share-Link */}
      <div className="panel" data-testid="share-link-panel">
        <h2 style={{ marginTop: 0 }}>Link zum Teilen</h2>
        <p className="muted small">
          Wer diesen Link kennt, kann die Statistik dieser Klausur (inklusive
          Bonuspunkte-Simulation) ohne Anmeldung ansehen — sonst nichts: keine Namen, keine
          Punkteeingabe, keine anderen Seiten.
        </p>
        <ErrorList messages={shareMessages} />
        {shareNotice !== null ? <SuccessNotice>{shareNotice}</SuccessNotice> : null}
        {exam?.share_token == null ? (
          <div className="button-row">
            <button
              type="button"
              data-testid="create-share-link"
              disabled={sharePending}
              onClick={() => void onCreateOrRegenerateShareLink()}
            >
              {sharePending ? "Wird erstellt …" : "Link erstellen"}
            </button>
          </div>
        ) : (
          <>
            <p>
              <code data-testid="share-link-value">{shareLinkUrl(exam.share_token)}</code>
            </p>
            <div className="button-row">
              <button
                type="button"
                data-testid="copy-share-link"
                onClick={() => void onCopyShareLink(exam.share_token as string)}
              >
                Link kopieren
              </button>
              <button
                type="button"
                data-testid="regenerate-share-link"
                disabled={sharePending}
                onClick={() => void onCreateOrRegenerateShareLink()}
              >
                {sharePending ? "Wird erstellt …" : "Neuen Link erstellen"}
              </button>
              <button
                type="button"
                data-testid="revoke-share-link"
                disabled={sharePending}
                onClick={() => void onRevokeShareLink()}
              >
                Link widerrufen
              </button>
            </div>
          </>
        )}
      </div>

      <StatisticsDashboard stats={stats} simulation={simulation} />
    </section>
  );
}
