import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent,
  type MouseEvent,
} from "react";
import { Link, useParams } from "react-router-dom";

import {
  errorMessages,
  getCompleteness,
  getExam,
  getPointsGrid,
  savePointsGrid,
  type CompletenessResult,
  type ExamDetail,
  type PointsEntry,
  type PointsExercise,
  type PointsGrid,
  type PointsRowWrite,
} from "../api/client";
import { ErrorList, SuccessNotice } from "../components/Messages";
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

type AttendedOption = "unknown" | "present" | "absent";

/** The tri-state control's option value <-> the wire's `boolean | null`. A plain checkbox
 * cannot express three states without an indeterminate hack that is also hard to test; a
 * `<select>` makes "nicht erfasst" a first-class, keyboard- and screen-reader-accessible option
 * rather than an implied default. */
function attendedToOption(attended: boolean | null): AttendedOption {
  if (attended === true) return "present";
  if (attended === false) return "absent";
  return "unknown";
}

function optionToAttended(option: string): boolean | null {
  if (option === "present") return true;
  if (option === "absent") return false;
  return null;
}

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

  const [completeness, setCompleteness] = useState<CompletenessResult | null>(null);
  const [completenessMessages, setCompletenessMessages] = useState<string[]>([]);

  // Keyed "<exerciseId>:<visible-row-index>" -> the input element, so Enter/Arrow keys can walk
  // a column without a full re-render pass. Cleaned up on unmount/filter change via the ref
  // callback below (an entry is deleted when React detaches it), so switching the course filter
  // never leaves stale, detached nodes that Enter would silently focus nothing on.
  const cellRefs = useRef<Map<string, HTMLInputElement>>(new Map());

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
      setRows(pointsGrid.entries.map((entry) => toEditableRow(entry, pointsGrid.exercises)));
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

  function updateBonus(registrationId: number, text: string): void {
    setRows((prev) =>
      prev.map((row) =>
        row.registrationId === registrationId ? { ...row, bonusPointsText: text } : row,
      ),
    );
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

  /** Walks in `direction` from `startRowIndex`, skipping disabled (not-attended) cells, and
   * focuses + selects the first enabled one it finds — so entering one exercise down a column
   * doesn't dead-end at the first not-attended student. Selecting the text on arrival is what
   * makes "typing over a focused cell replaces its content" true. */
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
        el.select();
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
      const saved = await savePointsGrid(examId, payload);
      // Re-seed from the server's recomputed rows rather than merging only the grade back in —
      // the server is authoritative (§8) for canonicalised values (e.g. a percentage-typed
      // "3," the instructor never finished) too, not just for the grade.
      setRows(saved.entries.map((entry) => toEditableRow(entry, grid.exercises)));
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
                <th scope="col">Anwesenheit</th>
                {grid.exercises.map((exercise) => (
                  <th scope="col" key={exercise.id} className="numeric-cell">
                    {exercise.name}
                    <br />
                    <span className="muted small">max. {formatDecimal(exercise.max_points)}</span>
                  </th>
                ))}
                <th scope="col" className="numeric-cell">
                  Bonus
                </th>
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
                  bonusMode: grid.bonus_mode,
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
                    <td className="numeric-cell">{row.versuch}</td>
                    <td>
                      <select
                        aria-label={`Anwesenheit ${row.vorname} ${row.nachname}`}
                        data-testid={`attended-${row.registrationId}`}
                        value={attendedToOption(row.attended)}
                        onChange={(event) =>
                          updateAttended(row.registrationId, optionToAttended(event.target.value))
                        }
                      >
                        <option value="unknown">Nicht erfasst</option>
                        <option value="present">Anwesend</option>
                        <option value="absent">Nicht anwesend</option>
                      </select>
                    </td>
                    {grid.exercises.map((exercise) => {
                      const text = row.pointsText[String(exercise.id)] ?? "";
                      const exceeds = cellExceedsMax(text, exercise.max_points);
                      const warnId = `overflow-${row.registrationId}-${exercise.id}`;
                      return (
                        <td key={exercise.id} className="numeric-cell">
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
                    <td className="numeric-cell">
                      <input
                        type="text"
                        inputMode="decimal"
                        className="bonus-input"
                        aria-label={`Bonuspunkte für ${row.vorname} ${row.nachname}`}
                        data-testid={`bonus-${row.registrationId}`}
                        value={row.bonusPointsText}
                        onChange={(event) => updateBonus(row.registrationId, event.target.value)}
                      />
                    </td>
                    <td className="numeric-cell" data-testid={`total-${row.registrationId}`}>
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
          <SuccessNotice>Alle Daten sind vollständig — Berichte können erzeugt werden.</SuccessNotice>
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
