import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type FocusEvent,
  type KeyboardEvent,
  type MouseEvent,
} from "react";
import { Link, useParams } from "react-router-dom";

import {
  downloadExaminationOfficeExcel,
  downloadExaminationOfficePdf,
  downloadStudentResultsExcel,
  downloadStudentResultsPdf,
  errorMessages,
  getCompleteness,
  getExam,
  getPointsGrid,
  savePointsGrid,
  updateExam,
  type BonusMode,
  type CompletenessResult,
  type DownloadedFile,
  type ExamDetail,
  type PointsEntry,
  type PointsExercise,
  type PointsGrid,
  type PointsRowWrite,
} from "../api/client";
import { BackButton } from "../components/BackButton";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { ErrorList, SuccessNotice } from "../components/Messages";
import { BONUS_MODE_OPTIONS } from "../grading/bonusMode";
import { compareDecimalStrings, computeGradePreview } from "../grading/preview";
import { EMPTY_DISPLAY, formatDecimal, parseDecimalInput } from "../util/format";
import { parseRouteId } from "../util/id";

/**
 * The locally-editable form of one `PointsEntry`. `pointsText` always has an entry for every
 * exercise the grid knows about — an **empty string means "not entered"**, never a stored `0` —
 * so a controlled `<input>` always has a defined value and the empty/zero distinction (§8.1)
 * lives in one place: whether the string is `""`.
 *
 * There is no per-row `warnings` field here: the bulk-save response carries a single flat
 * `warnings: string[]` for the whole batch (`BulkPointsSaveResult`), not one per row — see
 * `saveWarnings` state in the component instead, and `cellExceedsMax` for the *live*,
 * client-side, per-cell check that runs regardless of whether a save has happened yet.
 */
/** One of the four §10/§11 report downloads offered once the exam is export-ready. */
type ReportDownloadKind =
  | "examination-office-pdf"
  | "examination-office-excel"
  | "student-results-pdf"
  | "student-results-excel";

interface EditableRow {
  registrationId: number;
  matrikelnummer: string;
  nachname: string;
  vorname: string;
  courseCode: string;
  versuch: number;
  /** `null` = not yet recorded, distinct from `false` — §7.4/§8.1. */
  attended: boolean | null;
  bonusPointsText: string;
  pointsText: Record<string, string>;
}

function toEditableRow(entry: PointsEntry, exercises: readonly PointsExercise[]): EditableRow {
  const pointsText: Record<string, string> = {};
  for (const exercise of exercises) {
    const key = String(exercise.id);
    // Straight string assignment, no Number() — and a missing key becomes "", never "0".
    pointsText[key] = entry.points[key] ?? "";
  }
  return {
    registrationId: entry.id,
    matrikelnummer: entry.matrikelnummer,
    nachname: entry.nachname,
    vorname: entry.vorname,
    courseCode: entry.course_code,
    versuch: entry.versuch,
    attended: entry.attended,
    bonusPointsText: entry.bonus_points,
    pointsText,
  };
}

/** Attendance is edited as two independent radio buttons ("anwesend" / "nicht anwesend") rather
 * than a tri-state `<select>`: `row.attended === true` checks one, `=== false` checks the other,
 * and — critically — neither being checked is a perfectly valid HTML radio state, which maps
 * exactly onto `attended === null` ("nicht erfasst", §4/§8.1). There is no radio for "unknown",
 * so no click can ever *uncheck* a recorded value back to `null` by accident — only a fresh row
 * load or the bulk "mark present" action (which only ever touches already-`null` rows) produce
 * it. */

/**
 * The exercises a row currently has a parsable, non-empty value for, as canonical decimal
 * strings — ready for `computeGradePreview`. An empty or still-mid-keystroke cell is skipped
 * entirely rather than substituted with 0: unlike ExamDetailPage's max-points editor (where an
 * exercise always needs *some* value eventually), an unentered exercise point here is a real,
 * distinct, and entirely normal state (§8.1), not just a display nicety.
 */
function enteredPoints(row: EditableRow, exercises: readonly PointsExercise[]): string[] {
  const values: string[] = [];
  for (const exercise of exercises) {
    const text = row.pointsText[String(exercise.id)] ?? "";
    if (text.trim() === "") continue;
    const canonical = parseDecimalInput(text);
    if (canonical === null) continue;
    values.push(canonical);
  }
  return values;
}

/** Warn, never clamp (§8): true if the typed value is a parsable decimal strictly greater than
 * the exercise's max_points. An empty or unparsable (mid-keystroke) cell is never flagged. */
function cellExceedsMax(text: string, maxPoints: string): boolean {
  if (text.trim() === "") return false;
  const canonical = parseDecimalInput(text);
  if (canonical === null) return false;
  const cmp = compareDecimalStrings(canonical, maxPoints);
  return cmp !== null && cmp > 0;
}

/**
 * Builds the bulk-save payload from **every** row currently held in state, not just the ones
 * visible under the current course filter — the filter is client-side display only (same
 * convention as RegistrationsPage), and scoping the save to it would silently drop edits made
 * before switching the filter.
 *
 * A not-attended row's points are sent exactly as stored: disabling its inputs is presentation
 * only (§8), and this function must never null them out just because `attended === false`.
 */
function buildSavePayload(
  allRows: readonly EditableRow[],
  exercises: readonly PointsExercise[],
): PointsRowWrite[] {
  return allRows.map((row) => {
    const points: Record<string, string | null> = {};
    for (const exercise of exercises) {
      const key = String(exercise.id);
      const text = row.pointsText[key] ?? "";
      // The empty/zero distinction, in one place: an empty cell sends null ("not entered"); a
      // typed "0" is canonicalised and sent as "0" like any other value.
      points[key] = text.trim() === "" ? null : (parseDecimalInput(text) ?? text);
    }
    // Same empty/zero rule as points, but with `null` rather than a fabricated "0": the wire
    // contract treats an omitted/`null` bonus_points as "use the server's default of 0", so an
    // emptied field asks for exactly that default rather than sending the empty string, which
    // the decimal-string contract rejects outright (a request-wide 422, losing every row's
    // edits in this bulk save, not just this one field).
    const bonusText = row.bonusPointsText.trim();
    return {
      registration_id: row.registrationId,
      attended: row.attended,
      bonus_points: bonusText === "" ? null : (parseDecimalInput(bonusText) ?? bonusText),
      points,
    };
  });
}

/**
 * Derives the single, exam-wide bonus field's display state from every row currently held in
 * state. `bonus_points` itself stays per-registration in the database and on the wire (§7.3) —
 * this only decides what the *one* input above the grid should show.
 *
 * All rows sharing one value -> show it. Different values across rows (possible from earlier
 * per-row entry, or a copy-forward) -> show the field blank rather than silently picking one of
 * them, so the mixed state is visible instead of an instructor unknowingly overwriting data the
 * moment they touch the field.
 *
 * Grouped by **decimal value** via `compareDecimalStrings`, not by raw string equality: an
 * untouched row's `bonus_points` can come back from the server as `"0"` while an explicitly
 * entered zero comes back as `"0.00"` (same value, different canonical text some servers may
 * emit) — comparing strings would misreport that as "mixed" even though every student's bonus is
 * actually zero.
 */
function computeBonusField(rows: readonly EditableRow[]): {
  text: string;
  mixedCount: number | null;
} {
  if (rows.length === 0) return { text: "", mixedCount: null };
  const representatives: string[] = [];
  for (const row of rows) {
    const alreadySeen = representatives.some(
      (value) => compareDecimalStrings(value, row.bonusPointsText) === 0,
    );
    if (!alreadySeen) representatives.push(row.bonusPointsText);
  }
  if (representatives.length === 1) return { text: representatives[0] ?? "", mixedCount: null };
  return { text: "", mixedCount: representatives.length };
}

export default function PointsEntryPage(): JSX.Element {
  const params = useParams();
  const examId = parseRouteId(params["examId"]);

  const [exam, setExam] = useState<ExamDetail | null>(null);
  const [examMessages, setExamMessages] = useState<string[]>([]);

  const [grid, setGrid] = useState<PointsGrid | null>(null);
  const [rows, setRows] = useState<EditableRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [gridMessages, setGridMessages] = useState<string[]>([]);

  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  const [savedNotice, setSavedNotice] = useState(false);
  // The last bulk save's flat, batch-level warnings (`BulkPointsSaveResult.warnings` — there is
  // no per-row warnings field on the wire). Cleared on any further edit, same as `savedNotice`:
  // a warning describes the *previous* save's data, and must not linger once the instructor has
  // already changed the value it was about.
  const [saveWarnings, setSaveWarnings] = useState<string[]>([]);

  const [courseFilter, setCourseFilter] = useState("");

  // The shared "for the whole exam" bonus field (see `computeBonusField`) and the bulk
  // "alle als anwesend markieren" confirmation dialog's open/closed state.
  const [bonusFieldText, setBonusFieldText] = useState("");
  const [bonusMixedCount, setBonusMixedCount] = useState<number | null>(null);
  const [showBulkAttendDialog, setShowBulkAttendDialog] = useState(false);

  // The exam-wide bonus *mode* (§7.3) — moved here from ExamDetailPage since it governs how the
  // bonus points entered on this page are applied. Edited via radios like the exercise/attendance
  // grid above it, and committed by the same "Speichern" button (see `onSave`) rather than its
  // own save action, so it participates in the same dirty/unsaved-changes tracking as everything
  // else on this page.
  const [bonusMode, setBonusModeState] = useState<BonusMode>("ALWAYS");

  const [completeness, setCompleteness] = useState<CompletenessResult | null>(null);
  const [completenessMessages, setCompletenessMessages] = useState<string[]>([]);

  // §10/§11 report downloads. One key tracks which of the four buttons is in flight (rather than
  // a plain boolean) so its own label can say "Wird erstellt …" while the other three stay in
  // their normal state — same reasoning as `saving` above, just per-button instead of per-page.
  const [downloadingReport, setDownloadingReport] = useState<ReportDownloadKind | null>(null);
  const [reportDownloadMessages, setReportDownloadMessages] = useState<string[]>([]);

  // Keyed "<exerciseId>:<visible-row-index>" -> the input element, so Enter/Arrow keys can walk
  // a column without a full re-render pass. Cleaned up on unmount/filter change via the ref
  // callback below (an entry is deleted when React detaches it), so switching the course filter
  // never leaves stale, detached nodes that Enter would silently focus nothing on.
  const cellRefs = useRef<Map<string, HTMLInputElement>>(new Map());

  // Which element (if any) is mid-way through a "focusing click" — the mousedown that is about
  // to move focus into it. Its paired mouseup must be swallowed or the browser's default
  // behaviour (place the caret where the mouse is / collapse the selection) would immediately
  // undo the `select()` made in the focus handler below. A *second* click on an already-focused
  // input never sets this (no focus event fires), so its mouseup is left alone and the caret
  // places normally — "select on focus, not on every click".
  const selectOnMouseUpTarget = useRef<HTMLInputElement | null>(null);

  const reload = useCallback(async () => {
    if (examId === null) {
      setGridMessages(["Ungültige Adresse."]);
      setLoading(false);
      return;
    }
    setLoading(true);
    try {
      const [examDetail, pointsGrid] = await Promise.all([getExam(examId), getPointsGrid(examId)]);
      setExam(examDetail);
      setExamMessages([]);
      setGrid(pointsGrid);
      setBonusModeState(pointsGrid.bonus_mode);
      const mappedRows = pointsGrid.entries.map((entry) => toEditableRow(entry, pointsGrid.exercises));
      setRows(mappedRows);
      const bonusField = computeBonusField(mappedRows);
      setBonusFieldText(bonusField.text);
      setBonusMixedCount(bonusField.mixedCount);
      setGridMessages([]);
      setDirty(false);
      setSaveWarnings([]);
    } catch (error) {
      setGridMessages(errorMessages(error));
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

  // Tab-close/refresh guard. In-app navigation via this page's own breadcrumb links is guarded
  // separately below (onBreadcrumbClick) — react-router v6 without a data router has no
  // navigation-blocking hook (`useBlocker`) that would let this cover the browser back button
  // too, so this is deliberately the two mechanisms this page has, not a single unified one.
  useEffect(() => {
    function onBeforeUnload(event: BeforeUnloadEvent): void {
      if (!dirty) return;
      // preventDefault() alone is the current standard and is what every browser we target
      // honours; the legacy `returnValue = ""` assignment is deprecated and flagged by TS.
      event.preventDefault();
    }
    window.addEventListener("beforeunload", onBeforeUnload);
    return () => window.removeEventListener("beforeunload", onBeforeUnload);
  }, [dirty]);

  function onBreadcrumbClick(event: MouseEvent<HTMLAnchorElement>): void {
    if (dirty && !window.confirm("Es gibt ungespeicherte Änderungen. Trotzdem verlassen?")) {
      event.preventDefault();
    }
  }

  /* ------------------------------------------------------------------------------- editing */

  function updateAttended(registrationId: number, attended: boolean | null): void {
    setRows((prev) =>
      prev.map((row) => (row.registrationId === registrationId ? { ...row, attended } : row)),
    );
    setDirty(true);
    setSavedNotice(false);
    setSaveWarnings([]);
  }

  /** The single, exam-wide bonus field's edit handler: fans the typed text out to *every* row's
   * `bonusPointsText` — including rows hidden by the current course filter, same "state holds
   * every row" convention as `buildSavePayload` — and clears the mixed-value hint, since after
   * this edit every row shares one value by construction. Fanning out on every keystroke (rather
   * than on blur) keeps what's on screen always matching what a save would send; `bonus_points`
   * itself stays per-registration in the database and on the wire (§7.3), this is presentation
   * only. */
  function updateBonusAll(text: string): void {
    setBonusFieldText(text);
    setBonusMixedCount(null);
    setRows((prev) => prev.map((row) => ({ ...row, bonusPointsText: text })));
    setDirty(true);
    setSavedNotice(false);
    setSaveWarnings([]);
  }

  function updateBonusMode(mode: BonusMode): void {
    setBonusModeState(mode);
    setDirty(true);
    setSavedNotice(false);
    setSaveWarnings([]);
  }

  function updatePoint(registrationId: number, exerciseId: number, text: string): void {
    setRows((prev) =>
      prev.map((row) =>
        row.registrationId === registrationId
          ? { ...row, pointsText: { ...row.pointsText, [String(exerciseId)]: text } }
          : row,
      ),
    );
    setDirty(true);
    setSavedNotice(false);
    setSaveWarnings([]);
  }

  /* ---------------------------------------------------------------------------- keyboard nav */

  function cellKey(exerciseId: number, rowIndex: number): string {
    return `${exerciseId}:${rowIndex}`;
  }

  function registerCellRef(exerciseId: number, rowIndex: number, el: HTMLInputElement | null): void {
    const key = cellKey(exerciseId, rowIndex);
    if (el === null) cellRefs.current.delete(key);
    else cellRefs.current.set(key, el);
  }

  /** Selects an input's full contents, so typing immediately replaces the previous value instead
   * of inserting at wherever the cursor happened to land. One helper, reused for both arrival
   * paths: the keyboard column-navigation below (landing on a cell via Enter/Arrow) and the
   * point/bonus inputs' `onFocus` (landing via a mouse click or Tab) — see those call sites
   * rather than duplicating this. */
  function selectContents(el: HTMLInputElement): void {
    el.select();
  }

  /** Runs `select()` on arrival via mouse/Tab. Paired with `onCellMouseDown`/`onCellMouseUp`
   * below: without that pair, a real click's mouseup fires *after* this and would silently
   * collapse the selection back to a caret (mousedown -> focus -> **mouseup**), which the
   * keyboard-navigation path (Enter/Arrow, no mouse involved at all) never had to worry about. */
  function onSelectableFocus(event: FocusEvent<HTMLInputElement>): void {
    selectContents(event.currentTarget);
  }

  function onSelectableMouseDown(event: MouseEvent<HTMLInputElement>): void {
    const el = event.currentTarget;
    // Only a click that is *about* to move focus here needs its mouseup swallowed; a click on an
    // already-focused input must place the caret normally, or editing part of a value would be
    // impossible.
    selectOnMouseUpTarget.current = document.activeElement === el ? null : el;
  }

  function onSelectableMouseUp(event: MouseEvent<HTMLInputElement>): void {
    if (selectOnMouseUpTarget.current === event.currentTarget) {
      event.preventDefault();
      selectOnMouseUpTarget.current = null;
    }
  }

  /** Walks in `direction` from `startRowIndex`, skipping disabled (not-attended) cells, and
   * focuses + selects the first enabled one it finds — so entering one exercise down a column
   * doesn't dead-end at the first not-attended student. */
  function focusCell(
    exerciseId: number,
    startRowIndex: number,
    direction: 1 | -1,
    rowCount: number,
  ): void {
    let rowIndex = startRowIndex;
    while (rowIndex >= 0 && rowIndex < rowCount) {
      const el = cellRefs.current.get(cellKey(exerciseId, rowIndex));
      if (el !== undefined && !el.disabled) {
        el.focus();
        selectContents(el);
        return;
      }
      rowIndex += direction;
    }
  }

  function onCellKeyDown(
    exerciseId: number,
    rowIndex: number,
    rowCount: number,
    event: KeyboardEvent<HTMLInputElement>,
  ): void {
    if (event.key === "Enter") {
      // Never let Enter do anything else (e.g. submit an ancestor form) — this page's Enter key
      // is entirely repurposed for column navigation, matching the spreadsheet convention
      // (Enter/Shift+Enter = down/up the same column) rather than form submission.
      event.preventDefault();
      const direction = event.shiftKey ? -1 : 1;
      focusCell(exerciseId, rowIndex + direction, direction, rowCount);
    } else if (event.key === "ArrowDown") {
      event.preventDefault();
      focusCell(exerciseId, rowIndex + 1, 1, rowCount);
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      focusCell(exerciseId, rowIndex - 1, -1, rowCount);
    }
    // Tab/Shift+Tab are left to the browser's native tab order, which already walks the grid
    // left-to-right, top-to-bottom in DOM order — no custom handling needed.
  }

  /* ----------------------------------------------------------------------------------- save */

  async function onSave(): Promise<void> {
    if (examId === null || grid === null) return;
    setSaving(true);
    setGridMessages([]);
    setSavedNotice(false);
    try {
      const payload = buildSavePayload(rows, grid.exercises);
      // The bonus mode is a separate resource (the exam, not the points grid) on the wire, so a
      // changed mode needs its own PATCH alongside the points PUT — but both are committed
      // together here since this button is this page's one "Speichern" action.
      const bonusModeChanged = bonusMode !== grid.bonus_mode;
      const [saved] = await Promise.all([
        savePointsGrid(examId, payload),
        bonusModeChanged ? updateExam(examId, { bonus_mode: bonusMode }) : Promise.resolve(null),
      ]);
      // Re-seed from the server's recomputed rows rather than merging only the grade back in —
      // the server is authoritative (§8) for canonicalised values (e.g. a percentage-typed
      // "3," the instructor never finished) too, not just for the grade.
      const mappedRows = saved.entries.map((entry) => toEditableRow(entry, grid.exercises));
      setRows(mappedRows);
      const bonusField = computeBonusField(mappedRows);
      setBonusFieldText(bonusField.text);
      setBonusMixedCount(bonusField.mixedCount);
      if (bonusModeChanged) {
        setGrid((prev) => (prev === null ? prev : { ...prev, bonus_mode: bonusMode }));
      }
      setDirty(false);
      setSavedNotice(true);
      // Batch-level, not per-row (see BulkPointsSaveResult) — e.g. a value above an exercise's
      // max_points, already naming the student and exercise in its own German text.
      setSaveWarnings(saved.warnings);
      await reloadCompleteness();
    } catch (error) {
      setGridMessages(errorMessages(error));
    } finally {
      setSaving(false);
    }
  }

  /* -------------------------------------------------------------------------- report downloads */

  /**
   * Shared by all four §10/§11 report buttons: blob -> `URL.createObjectURL` -> a temporary
   * `<a download>` click -> revoke, same pattern as `RegistrationsPage`'s
   * `onDownloadAttendanceList`. A stale-UI `409` (the exam stopped being export-ready between
   * this page loading and the click) surfaces through the same `errorMessages`/`ErrorList` path
   * as every other error on this page rather than a bespoke one.
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

  /* ---------------------------------------------------------------------------- derived data */

  const courseCodes = useMemo(
    () => Array.from(new Set(rows.map((row) => row.courseCode))).sort(),
    [rows],
  );

  // Filtering only, never re-sorting: the server sorts the grid by Matrikelnummer
  // (`read_points_grid`), which is *not* the same order as the §6 attendance list/RegistrationsPage
  // (course, then Nachname, then Vorname, DIN 5007-1) — a client-side re-sort here would just
  // substitute one order for another rather than have an authoritative source, so it stays as
  // the server sent it.
  const visibleRows = useMemo(
    () => (courseFilter === "" ? rows : rows.filter((row) => row.courseCode === courseFilter)),
    [rows, courseFilter],
  );

  // Scoped to the currently visible (filter-respecting) rows, same as the bulk action below —
  // a table-header bulk control acting on rows the instructor can't currently see would be
  // surprising.
  const unrecordedVisibleCount = useMemo(
    () => visibleRows.filter((row) => row.attended === null).length,
    [visibleRows],
  );

  /** Sets every currently visible row whose attendance is still `null` to `true` — never touches
   * a row already explicitly `false`, so confirming this dialog can't silently turn a recorded
   * absence into a presence (the dialog's own text says the same thing to the instructor). */
  function applyBulkAttendance(): void {
    const targetIds = new Set(
      visibleRows.filter((row) => row.attended === null).map((row) => row.registrationId),
    );
    if (targetIds.size === 0) return;
    setRows((prev) =>
      prev.map((row) => (targetIds.has(row.registrationId) ? { ...row, attended: true } : row)),
    );
    setDirty(true);
    setSavedNotice(false);
    setSaveWarnings([]);
  }

  // A compact, row-height-preserving alternative to a field-error span under every offending
  // cell (which would blow up a dense grid's row height): the cell itself is marked (border +
  // aria-invalid/aria-describedby), and the actual German text lives here instead. This is the
  // *live* check, recomputed on every render regardless of whether a save has happened yet —
  // `saveWarnings` (state, set from the last save's response) is shown separately below.
  const overflowWarnings = useMemo(() => {
    if (grid === null) return [];
    const items: string[] = [];
    for (const row of rows) {
      for (const exercise of grid.exercises) {
        const text = row.pointsText[String(exercise.id)] ?? "";
        if (cellExceedsMax(text, exercise.max_points)) {
          const value = parseDecimalInput(text) ?? text;
          items.push(
            `${row.nachname}, ${row.vorname} (${row.matrikelnummer}) — ${exercise.name}: ` +
              `${formatDecimal(value)} überschreitet die Höchstpunktzahl ${formatDecimal(exercise.max_points)}.`,
          );
        }
      }
    }
    return items;
  }, [rows, grid]);

  if (loading) return <p className="muted">Wird geladen …</p>;

  if (examId === null || grid === null) {
    return (
      <section>
        <ErrorList messages={gridMessages} />
        <Link to="/">Zur Vorlesungsübersicht</Link>
      </section>
    );
  }

  return (
    <section>
      <div className="breadcrumb-row">
        <BackButton
          to={exam !== null ? `/klausuren/${exam.id}` : null}
          onClick={onBreadcrumbClick}
        />
        <p className="breadcrumb">
          <Link to="/" onClick={onBreadcrumbClick}>
            Vorlesungen
          </Link>
          {exam !== null ? (
            <>
              {" "}
              /{" "}
              <Link to={`/vorlesungen/${exam.lecture_id}`} onClick={onBreadcrumbClick}>
                {exam.lecture_name}
              </Link>{" "}
              /{" "}
              <Link to={`/klausuren/${exam.id}`} onClick={onBreadcrumbClick}>
                {exam.semester}, {exam.termin}
              </Link>{" "}
              / Punkte
            </>
          ) : null}
        </p>
      </div>
      <h1>Punkte &amp; Anwesenheit{exam !== null ? ` — ${exam.lecture_name}` : ""}</h1>
      <ErrorList messages={examMessages} />

      <div className="points-grid-toolbar">
        <div>
          <label htmlFor="course-filter">Studiengang</label>
          <select
            id="course-filter"
            className="medium"
            value={courseFilter}
            onChange={(event) => setCourseFilter(event.target.value)}
          >
            <option value="">Alle Studiengänge</option>
            {courseCodes.map((code) => (
              <option key={code} value={code}>
                {code}
              </option>
            ))}
          </select>
        </div>
        <div>
          <button
            type="button"
            disabled={unrecordedVisibleCount === 0}
            onClick={() => setShowBulkAttendDialog(true)}
            data-testid="bulk-mark-present"
          >
            Alle als anwesend markieren
          </button>
        </div>
        <div className="button-row">
          <button
            type="button"
            className="primary"
            disabled={saving || !dirty}
            onClick={() => void onSave()}
          >
            {saving ? "Wird gespeichert …" : "Speichern"}
          </button>
          {dirty ? (
            <span className="unsaved-indicator" data-testid="unsaved-indicator">
              Ungespeicherte Änderungen
            </span>
          ) : null}
        </div>
      </div>

      {showBulkAttendDialog ? (
        <ConfirmDialog
          title="Alle als anwesend markieren?"
          confirmLabel="Anwenden"
          onCancel={() => setShowBulkAttendDialog(false)}
          onConfirm={() => {
            applyBulkAttendance();
            setShowBulkAttendDialog(false);
          }}
        >
          <p>
            Dies markiert <strong>{unrecordedVisibleCount}</strong>{" "}
            {unrecordedVisibleCount === 1
              ? "Studierende bzw. Studierenden, deren Anwesenheit noch nicht erfasst ist,"
              : "Studierende, deren Anwesenheit noch nicht erfasst ist,"}{" "}
            als anwesend
            {courseFilter !== "" ? (
              <>
                {" "}
                — beschränkt auf den aktuell gefilterten Studiengang „{courseFilter}“
              </>
            ) : null}
            . Bereits als „nicht anwesend“ erfasste Studierende werden dabei{" "}
            <strong>nicht</strong> verändert.
          </p>
        </ConfirmDialog>
      ) : null}

      <fieldset className="points-grid-bonus">
        <legend>Bonuspunkte</legend>
        {BONUS_MODE_OPTIONS.map((option) => (
          <div className="radio-option" key={option.value}>
            <input
              id={`bonus-mode-${option.value}`}
              type="radio"
              name="bonus-mode"
              value={option.value}
              checked={bonusMode === option.value}
              onChange={() => updateBonusMode(option.value)}
            />{" "}
            <label htmlFor={`bonus-mode-${option.value}`}>{option.label}</label>
            <span className="explanation">{option.explanation}</span>
          </div>
        ))}
        <div className="field">
          <label htmlFor="bonus-all">Bonuspunkte (für alle Studierenden)</label>
          <input
            id="bonus-all"
            type="text"
            inputMode="decimal"
            className="narrow"
            data-testid="bonus-all"
            value={bonusFieldText}
            placeholder={bonusMixedCount !== null ? "uneinheitlich" : undefined}
            onChange={(event) => updateBonusAll(event.target.value)}
            onFocus={onSelectableFocus}
            onMouseDown={onSelectableMouseDown}
            onMouseUp={onSelectableMouseUp}
          />
        </div>
        {bonusMixedCount !== null ? (
          <p className="muted small" data-testid="bonus-mixed-hint">
            Aktuell sind {bonusMixedCount} unterschiedliche Bonuspunkte-Werte hinterlegt (z. B.
            durch frühere Eingaben oder eine übernommene Klausur) — das Feld bleibt deshalb leer.
            Eine Eingabe hier setzt den Wert für <strong>alle</strong> Studierenden.
          </p>
        ) : null}
      </fieldset>

      <div data-testid="grid-errors">
        <ErrorList
          messages={gridMessages}
          title={gridMessages.length > 1 ? "Bitte prüfen:" : undefined}
        />
      </div>
      {savedNotice ? <SuccessNotice>Die Änderungen wurden gespeichert.</SuccessNotice> : null}
      {!grid.grading_configured ? (
        <p className="muted small">
          Der Notenschlüssel ist noch nicht konfiguriert — es kann noch keine Notenvorschau
          berechnet werden.
        </p>
      ) : null}

      {overflowWarnings.length > 0 ? (
        <div className="points-grid-warnings" data-testid="overflow-warnings" role="alert">
          <strong>Hinweise:</strong>
          <ul>
            {overflowWarnings.map((message, index) => (
              <li key={`${index}-${message}`}>{message}</li>
            ))}
          </ul>
        </div>
      ) : null}

      {saveWarnings.length > 0 ? (
        <div className="points-grid-warnings" data-testid="save-warnings" role="alert">
          <strong>Hinweise der letzten Speicherung:</strong>
          <ul>
            {saveWarnings.map((message, index) => (
              <li key={`${index}-${message}`}>{message}</li>
            ))}
          </ul>
        </div>
      ) : null}

      {visibleRows.length === 0 ? (
        <p className="muted">Keine Studierenden für diese Auswahl.</p>
      ) : (
        <div className="points-grid-wrapper">
          <table className="points-grid">
            <thead>
              <tr>
                <th scope="col" className="col-sticky-1">
                  Matr.-Nr.
                </th>
                <th scope="col" className="col-sticky-2">
                  Nachname / Vorname
                </th>
                <th scope="col">Vers.</th>
                <th scope="col" className="attendance-header">
                  Anwesend
                  <span className="attendance-header-labels" aria-hidden="true">
                    <span>Ja</span>
                    <span>Nein</span>
                  </span>
                </th>
                {grid.exercises.map((exercise) => (
                  <th scope="col" key={exercise.id} className="numeric-cell exercise-header">
                    {exercise.name}
                    <br />
                    <span className="muted small">max. {formatDecimal(exercise.max_points)}</span>
                  </th>
                ))}
                <th scope="col" className="numeric-cell">
                  Summe
                </th>
                <th scope="col">Note (Vorschau)</th>
              </tr>
            </thead>
            <tbody>
              {visibleRows.map((row, rowIndex) => {
                const preview = computeGradePreview({
                  enteredExercisePoints: enteredPoints(row, grid.exercises),
                  bonusPoints: parseDecimalInput(row.bonusPointsText) ?? "0.00",
                  bonusMode,
                  attended: row.attended,
                  gradingSchema: grid.grading_schema,
                  gradingConfigured: grid.grading_configured,
                });
                const rowClasses = [
                  row.attended === false ? "row-not-attended" : "",
                  row.attended === null ? "row-attendance-unknown" : "",
                ]
                  .filter((value) => value !== "")
                  .join(" ");
                return (
                  <tr
                    key={row.registrationId}
                    className={rowClasses}
                    data-testid={`points-row-${row.registrationId}`}
                  >
                    <td className="col-sticky-1">{row.matrikelnummer}</td>
                    <td className="col-sticky-2">
                      {row.nachname}, {row.vorname}
                    </td>
                    <td className="numeric-cell cell-left">{row.versuch}</td>
                    <td>
                      <div
                        className="attendance-radio-group"
                        role="radiogroup"
                        aria-label={`Anwesenheit ${row.vorname} ${row.nachname}`}
                      >
                        <input
                          type="radio"
                          name={`attended-${row.registrationId}`}
                          data-testid={`attended-${row.registrationId}-present`}
                          aria-label={`Anwesend: ${row.nachname}, ${row.vorname}`}
                          checked={row.attended === true}
                          onChange={() => updateAttended(row.registrationId, true)}
                        />
                        <input
                          type="radio"
                          name={`attended-${row.registrationId}`}
                          data-testid={`attended-${row.registrationId}-absent`}
                          aria-label={`Nicht anwesend: ${row.nachname}, ${row.vorname}`}
                          checked={row.attended === false}
                          onChange={() => updateAttended(row.registrationId, false)}
                        />
                      </div>
                    </td>
                    {grid.exercises.map((exercise) => {
                      const text = row.pointsText[String(exercise.id)] ?? "";
                      const exceeds = cellExceedsMax(text, exercise.max_points);
                      const warnId = `overflow-${row.registrationId}-${exercise.id}`;
                      return (
                        <td key={exercise.id} className="numeric-cell cell-left">
                          {/* type="text", never type="number" (§7.0): a number input hands back
                              valueAsNumber and normalises what was typed. */}
                          <input
                            type="text"
                            inputMode="decimal"
                            className={exceeds ? "cell-input cell-warn" : "cell-input"}
                            aria-label={`${exercise.name} für ${row.vorname} ${row.nachname}`}
                            aria-invalid={exceeds ? "true" : undefined}
                            aria-describedby={exceeds ? warnId : undefined}
                            data-testid={`point-${row.registrationId}-${exercise.id}`}
                            disabled={row.attended === false}
                            value={text}
                            onChange={(event) =>
                              updatePoint(row.registrationId, exercise.id, event.target.value)
                            }
                            onKeyDown={(event) =>
                              onCellKeyDown(exercise.id, rowIndex, visibleRows.length, event)
                            }
                            onFocus={onSelectableFocus}
                            onMouseDown={onSelectableMouseDown}
                            onMouseUp={onSelectableMouseUp}
                            ref={(el) => registerCellRef(exercise.id, rowIndex, el)}
                          />
                          {exceeds ? (
                            <span id={warnId} className="cell-warning-text">
                              &gt; max. {formatDecimal(exercise.max_points)}
                            </span>
                          ) : null}
                        </td>
                      );
                    })}
                    <td className="numeric-cell cell-left" data-testid={`total-${row.registrationId}`}>
                      {preview.finalTotal === null ? EMPTY_DISPLAY : formatDecimal(preview.finalTotal)}
                    </td>
                    <td data-testid={`grade-${row.registrationId}`}>{preview.gradeLabel}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      <div className="panel">
        <h2 style={{ marginTop: 0 }}>Vollständigkeit</h2>
        <p className="small muted">
          Vor einem Bericht für das Prüfungsamt oder die Studierenden (§10, §11) müssen alle
          Anwesenheiten und — bei anwesenden Studierenden — alle Aufgabenpunkte erfasst sein.
        </p>
        <div data-testid="completeness-errors">
          <ErrorList messages={completenessMessages} />
        </div>
        {completeness === null ? (
          <p className="muted">Wird geladen …</p>
        ) : completeness.is_complete ? (
          <>
            <SuccessNotice>Alle Daten sind vollständig — Berichte können erzeugt werden.</SuccessNotice>
            {grid.grading_configured ? (
              <>
                <div data-testid="report-download-errors">
                  <ErrorList messages={reportDownloadMessages} />
                </div>
                <div className="button-row">
                  <button
                    type="button"
                    data-testid="download-examination-office-pdf"
                    disabled={downloadingReport !== null}
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
                    disabled={downloadingReport !== null}
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
                    disabled={downloadingReport !== null}
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
                    disabled={downloadingReport !== null}
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
            ) : (
              <p className="muted small" data-testid="schema-not-configured-hint">
                Der Notenschlüssel ist noch nicht vollständig konfiguriert.
              </p>
            )}
          </>
        ) : (
          <>
            <p>
              <strong>{completeness.incomplete_count}</strong>{" "}
              {completeness.incomplete_count === 1
                ? "Studierende bzw. Studierender ist"
                : "Studierende sind"}{" "}
              noch unvollständig:
            </p>
            <ul data-testid="completeness-list">
              {completeness.incomplete_students.map((entry) => {
                const parts: string[] = [];
                if (entry.attendance_missing) parts.push("Anwesenheit nicht erfasst");
                // Already exercise *names* (the backend resolves ids to names) — no lookup needed.
                if (entry.missing_exercises.length > 0) {
                  parts.push(`fehlende Punkte: ${entry.missing_exercises.join(", ")}`);
                }
                return (
                  <li key={entry.id} data-testid={`incomplete-${entry.id}`}>
                    {entry.nachname}, {entry.vorname} (Matr.-Nr. {entry.matrikelnummer}):{" "}
                    {parts.join("; ")}
                  </li>
                );
              })}
            </ul>
          </>
        )}
      </div>
    </section>
  );
}
