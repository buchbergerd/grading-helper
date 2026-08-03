import {
  Fragment,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ChangeEvent,
  type FormEvent,
  type JSX,
} from "react";
import { Link, useParams } from "react-router-dom";

import {
  countRegistrations,
  createRegistration,
  deleteAllRegistrations,
  deleteRegistration,
  downloadAttendanceList,
  errorMessages,
  extractDuplicates,
  getExam,
  importRegistrations,
  listRegistrations,
  updateRegistration,
  type AttendanceListSortOrder,
  type DuplicateMatrikelnummer,
  type ExamDetail,
  type RegistrationHeadCount,
  type RegistrationImportResult,
  type RegistrationOut,
} from "../api/client";
import { BackButton } from "../components/BackButton";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { IconEdit, IconExclude, IconInclude, IconTrash } from "../components/icons";
import { ErrorList, SuccessNotice } from "../components/Messages";
import { EMPTY_DISPLAY, pluralize } from "../util/format";
import { parsePositiveInteger, parseRouteId } from "../util/id";

/** Editable fields of the inline "add" form. `versuch` is kept as text, per the decimal rule's
 * sibling policy for small integers: parsed only via `parsePositiveInteger`, never `Number()`
 * inline here. */
interface AddForm {
  matrikelnummer: string;
  nachname: string;
  vorname: string;
  courseCode: string;
  moduleTitle: string;
  versuchText: string;
  kommentar: string;
}

const EMPTY_ADD_FORM: AddForm = {
  matrikelnummer: "",
  nachname: "",
  vorname: "",
  courseCode: "",
  moduleTitle: "",
  versuchText: "1",
  kommentar: "",
};

interface EditForm {
  matrikelnummer: string;
  nachname: string;
  vorname: string;
  courseCode: string;
  moduleTitle: string;
  versuchText: string;
  kommentar: string;
}

function toEditForm(row: RegistrationOut): EditForm {
  return {
    matrikelnummer: row.matrikelnummer,
    nachname: row.nachname,
    vorname: row.vorname,
    courseCode: row.course_code,
    moduleTitle: row.module_title,
    versuchText: String(row.versuch),
    kommentar: row.kommentar ?? "",
  };
}

/** The four sort orders offered for the attendance-list PDF, in the order shown to the
 * instructor. `"course_nachname"` is §6's default (course, then Nachname). */
const ATTENDANCE_SORT_OPTIONS: { value: AttendanceListSortOrder; label: string }[] = [
  { value: "nachname", label: "Nachname" },
  { value: "matrikelnummer", label: "Matrikelnummer" },
  { value: "course_nachname", label: "Studiengang, dann Nachname" },
  { value: "course_matrikelnummer", label: "Studiengang, dann Matrikelnummer" },
];

export default function RegistrationsPage(): JSX.Element {
  const params = useParams();
  const examId = parseRouteId(params["examId"]);

  const [exam, setExam] = useState<ExamDetail | null>(null);
  const [examMessages, setExamMessages] = useState<string[]>([]);

  const [registrations, setRegistrations] = useState<RegistrationOut[]>([]);
  const [headCount, setHeadCount] = useState<RegistrationHeadCount | null>(null);
  const [loadingList, setLoadingList] = useState(true);
  const [listMessages, setListMessages] = useState<string[]>([]);

  const [courseFilter, setCourseFilter] = useState("");
  const [showExcluded, setShowExcluded] = useState(true);

  /* ------------------------------------------------------------------------------- import */
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [files, setFiles] = useState<File[]>([]);
  const [replaceExisting, setReplaceExisting] = useState(false);
  const [importing, setImporting] = useState(false);
  const [importMessages, setImportMessages] = useState<string[]>([]);
  const [importDuplicates, setImportDuplicates] = useState<DuplicateMatrikelnummer[] | null>(
    null,
  );
  const [importResult, setImportResult] = useState<RegistrationImportResult | null>(null);

  /* ------------------------------------------------------------------------- manual add form */
  const [addForm, setAddForm] = useState<AddForm>(EMPTY_ADD_FORM);
  const [adding, setAdding] = useState(false);
  const [addMessages, setAddMessages] = useState<string[]>([]);

  /* ------------------------------------------------------------------------------- inline edit */
  const [editingId, setEditingId] = useState<number | null>(null);
  const [editForm, setEditForm] = useState<EditForm | null>(null);
  const [savingEdit, setSavingEdit] = useState(false);
  const [editMessages, setEditMessages] = useState<string[]>([]);

  const [togglingId, setTogglingId] = useState<number | null>(null);

  const [pendingDeleteId, setPendingDeleteId] = useState<number | null>(null);
  const [deleting, setDeleting] = useState(false);

  /* -------------------------------------------------------------------------- delete all (§5.3) */
  const [confirmingDeleteAll, setConfirmingDeleteAll] = useState(false);
  const [deletingAll, setDeletingAll] = useState(false);

  const [downloading, setDownloading] = useState(false);
  const [downloadMessages, setDownloadMessages] = useState<string[]>([]);
  const [attendanceSortOrder, setAttendanceSortOrder] =
    useState<AttendanceListSortOrder>("course_nachname");

  const reloadExam = useCallback(async () => {
    if (examId === null) return;
    try {
      setExam(await getExam(examId));
      setExamMessages([]);
    } catch (error) {
      setExamMessages(errorMessages(error));
    }
  }, [examId]);

  const reloadRegistrations = useCallback(async () => {
    if (examId === null) {
      setListMessages(["Ungültige Adresse."]);
      setLoadingList(false);
      return;
    }
    setLoadingList(true);
    try {
      // Fetched unfiltered on purpose: course filter and the excluded toggle are applied
      // client-side over the one result, so switching either never needs a round trip and the
      // dropdown of course codes below always sees every course, not just the filtered one.
      const [regs, count] = await Promise.all([
        listRegistrations(examId),
        countRegistrations(examId),
      ]);
      setRegistrations(regs);
      setHeadCount(count);
      setListMessages([]);
    } catch (error) {
      setListMessages(errorMessages(error));
    } finally {
      setLoadingList(false);
    }
  }, [examId]);

  useEffect(() => {
    void reloadExam();
    void reloadRegistrations();
  }, [reloadExam, reloadRegistrations]);

  const courseCodes = useMemo(
    () => Array.from(new Set(registrations.map((row) => row.course_code))).sort(),
    [registrations],
  );

  // Never re-sorted here: the server already returns German-collated (course, Nachname,
  // Vorname) order (§6's default) — a client-side sort (e.g. by Matr.-Nr.) would diverge from
  // it and mislead. The attendance-list PDF's own sort order is chosen independently below, via
  // the radio buttons next to its download button.
  const visibleRegistrations = registrations.filter(
    (row) =>
      (courseFilter === "" || row.course_code === courseFilter) && (showExcluded || !row.excluded),
  );

  /* ------------------------------------------------------------------------------- import */

  function onFilesChosen(event: ChangeEvent<HTMLInputElement>): void {
    setFiles(Array.from(event.target.files ?? []));
  }

  async function onImport(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    if (examId === null) return;
    if (files.length === 0) {
      setImportMessages(["Bitte mindestens eine PDF-Datei auswählen."]);
      setImportDuplicates(null);
      setImportResult(null);
      return;
    }
    setImporting(true);
    setImportMessages([]);
    setImportDuplicates(null);
    setImportResult(null);
    try {
      const result = await importRegistrations(examId, files, replaceExisting);
      setImportResult(result);
      setFiles([]);
      if (fileInputRef.current) fileInputRef.current.value = "";
      await reloadRegistrations();
    } catch (error) {
      setImportMessages(errorMessages(error));
      setImportDuplicates(extractDuplicates(error));
    } finally {
      setImporting(false);
    }
  }

  /* ------------------------------------------------------------------------- manual add form */

  async function onAdd(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    if (examId === null) return;

    const problems: string[] = [];
    if (addForm.matrikelnummer.trim() === "") problems.push("Bitte eine Matrikelnummer angeben.");
    if (addForm.nachname.trim() === "") problems.push("Bitte einen Nachnamen angeben.");
    if (addForm.vorname.trim() === "") problems.push("Bitte einen Vornamen angeben.");
    if (addForm.courseCode.trim() === "") problems.push("Bitte einen Studiengang angeben.");
    if (addForm.moduleTitle.trim() === "") problems.push("Bitte den Modultitel angeben.");
    const versuch = parsePositiveInteger(addForm.versuchText);
    if (versuch === null) problems.push("Der Versuch muss eine positive ganze Zahl sein.");
    if (problems.length > 0) {
      setAddMessages(problems);
      return;
    }
    if (versuch === null) return; // unreachable (already caught above); narrows the type for TS

    setAdding(true);
    try {
      await createRegistration(examId, {
        matrikelnummer: addForm.matrikelnummer.trim(),
        nachname: addForm.nachname.trim(),
        vorname: addForm.vorname.trim(),
        course_code: addForm.courseCode.trim(),
        module_title: addForm.moduleTitle.trim(),
        versuch,
        kommentar: addForm.kommentar.trim() === "" ? undefined : addForm.kommentar.trim(),
      });
      setAddForm(EMPTY_ADD_FORM);
      setAddMessages([]);
      await reloadRegistrations();
    } catch (error) {
      setAddMessages(errorMessages(error));
    } finally {
      setAdding(false);
    }
  }

  /* ------------------------------------------------------------------------------- inline edit */

  function onStartEdit(row: RegistrationOut): void {
    setEditingId(row.id);
    setEditForm(toEditForm(row));
    setEditMessages([]);
  }

  function onCancelEdit(): void {
    setEditingId(null);
    setEditForm(null);
    setEditMessages([]);
  }

  async function onSaveEdit(id: number): Promise<void> {
    if (editForm === null) return;

    const problems: string[] = [];
    if (editForm.matrikelnummer.trim() === "") problems.push("Bitte eine Matrikelnummer angeben.");
    if (editForm.nachname.trim() === "") problems.push("Bitte einen Nachnamen angeben.");
    if (editForm.vorname.trim() === "") problems.push("Bitte einen Vornamen angeben.");
    if (editForm.courseCode.trim() === "") problems.push("Bitte einen Studiengang angeben.");
    if (editForm.moduleTitle.trim() === "") problems.push("Bitte den Modultitel angeben.");
    const versuch = parsePositiveInteger(editForm.versuchText);
    if (versuch === null) problems.push("Der Versuch muss eine positive ganze Zahl sein.");
    if (problems.length > 0) {
      setEditMessages(problems);
      return;
    }
    if (versuch === null) return; // unreachable (already caught above); narrows the type for TS

    setSavingEdit(true);
    try {
      await updateRegistration(id, {
        matrikelnummer: editForm.matrikelnummer.trim(),
        nachname: editForm.nachname.trim(),
        vorname: editForm.vorname.trim(),
        course_code: editForm.courseCode.trim(),
        module_title: editForm.moduleTitle.trim(),
        versuch,
        // An emptied field explicitly clears the remark (the API's documented semantic for a
        // present `null`), rather than being omitted and left unchanged.
        kommentar: editForm.kommentar.trim() === "" ? null : editForm.kommentar.trim(),
      });
      setEditingId(null);
      setEditForm(null);
      setEditMessages([]);
      await reloadRegistrations();
    } catch (error) {
      setEditMessages(errorMessages(error));
    } finally {
      setSavingEdit(false);
    }
  }

  /* ---------------------------------------------------------------------------- exclude toggle */

  async function onToggleExcluded(row: RegistrationOut): Promise<void> {
    setTogglingId(row.id);
    try {
      // Minimal patch body — only the one field being changed, never the whole row echoed
      // back, so nothing here can accidentally overwrite e.g. `flagged`.
      await updateRegistration(row.id, { excluded: !row.excluded });
      await reloadRegistrations();
    } catch (error) {
      setListMessages(errorMessages(error));
    } finally {
      setTogglingId(null);
    }
  }

  /* ----------------------------------------------------------------------------------- delete */

  async function onConfirmDelete(): Promise<void> {
    if (pendingDeleteId === null) return;
    setDeleting(true);
    try {
      await deleteRegistration(pendingDeleteId);
      setPendingDeleteId(null);
      await reloadRegistrations();
    } catch (error) {
      setListMessages(errorMessages(error));
      setPendingDeleteId(null);
    } finally {
      setDeleting(false);
    }
  }

  const pendingDeleteRow = registrations.find((row) => row.id === pendingDeleteId) ?? null;

  /* -------------------------------------------------------------------------- delete all (§5.3) */

  async function onConfirmDeleteAll(): Promise<void> {
    if (examId === null) return;
    setDeletingAll(true);
    try {
      await deleteAllRegistrations(examId);
      setConfirmingDeleteAll(false);
      await reloadRegistrations();
    } catch (error) {
      setListMessages(errorMessages(error));
      setConfirmingDeleteAll(false);
    } finally {
      setDeletingAll(false);
    }
  }

  /* --------------------------------------------------------------------------- attendance list */

  async function onDownloadAttendanceList(): Promise<void> {
    if (examId === null) return;
    setDownloading(true);
    setDownloadMessages([]);
    try {
      const { blob, filename } = await downloadAttendanceList(examId, attendanceSortOrder);
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
              / Anmeldungen
            </>
          ) : null}
        </p>
      </div>
      <h1>Anmeldungen{exam !== null ? ` — ${exam.lecture_name}` : ""}</h1>
      <ErrorList messages={examMessages} />

      {/* ---------------------------------------------------------------------------- import */}
      <div className="panel">
        <h2 style={{ marginTop: 0 }}>Anmeldungen importieren</h2>
        <p className="small muted">
          Ein PDF pro Studiengang; es können mehrere Dateien für dieselbe Klausur ausgewählt und
          gemeinsam hochgeladen werden.
        </p>
        <form onSubmit={(event) => void onImport(event)}>
          <div className="field">
            <label htmlFor="import-files">PDF-Dateien</label>
            <input
              id="import-files"
              ref={fileInputRef}
              type="file"
              accept="application/pdf"
              multiple
              onChange={onFilesChosen}
            />
          </div>
          <div className="field">
            <label htmlFor="replace-existing" style={{ display: "inline" }}>
              <input
                id="replace-existing"
                type="checkbox"
                checked={replaceExisting}
                onChange={(event) => setReplaceExisting(event.target.checked)}
              />{" "}
              Vorhandene Anmeldungen dieser Studiengänge ersetzen
            </label>
            <span className="explanation small muted" style={{ display: "block" }}>
              Löscht vor dem Import alle bereits gespeicherten Anmeldungen der Studiengänge, die
              in den ausgewählten Dateien vorkommen — einschließlich der zugehörigen
              Ausschluss-, Anwesenheits- und Punkteentscheidungen. Anmeldungen anderer
              Studiengänge bleiben unberührt. Ohne diese Option führt eine bereits vorhandene
              Matrikelnummer zum Abbruch des Imports.
            </span>
          </div>
          <button type="submit" className="primary" disabled={importing}>
            {importing ? "Wird importiert …" : "Importieren"}
          </button>
        </form>

        {importMessages.length > 0 ? (
          <div data-testid="import-errors" style={{ marginTop: "0.75rem" }}>
            <p style={{ fontWeight: 600, color: "var(--danger)" }}>
              Der Import wurde abgebrochen. Es wurde nichts importiert.
            </p>
            <ErrorList messages={importMessages} />
            {importDuplicates !== null && importDuplicates.length > 0 ? (
              <table>
                <caption className="small muted" style={{ textAlign: "left" }}>
                  Doppelt vorkommende Matrikelnummern
                </caption>
                <thead>
                  <tr>
                    <th scope="col">Matr.-Nr.</th>
                    <th scope="col">Studiengang</th>
                    <th scope="col">Herkunft</th>
                  </tr>
                </thead>
                <tbody>
                  {importDuplicates.flatMap((duplicate) =>
                    duplicate.occurrences.map((occurrence, index) => (
                      <tr key={`${duplicate.matrikelnummer}-${index}`}>
                        <td>{index === 0 ? duplicate.matrikelnummer : ""}</td>
                        <td>{occurrence.course_code}</td>
                        <td>
                          {occurrence.source === "upload"
                            ? `Hochgeladene Datei${occurrence.filename !== null ? ` „${occurrence.filename}“` : ""}`
                            : `Bereits importiert${occurrence.filename !== null ? ` aus „${occurrence.filename}“` : ""}`}
                        </td>
                      </tr>
                    )),
                  )}
                </tbody>
              </table>
            ) : null}
          </div>
        ) : null}

        {importResult !== null ? (
          <div data-testid="import-success" style={{ marginTop: "0.75rem" }}>
            <SuccessNotice>
              {pluralize(importResult.imported_total, "Anmeldung importiert", "Anmeldungen importiert")}
              {importResult.replaced_count > 0
                ? `, ${importResult.replaced_count} vorhandene ersetzt`
                : ""}
              .
            </SuccessNotice>
            <table>
              <thead>
                <tr>
                  <th scope="col">Datei</th>
                  <th scope="col">Studiengang</th>
                  <th scope="col">Modultitel</th>
                  <th scope="col">Zeilen</th>
                  <th scope="col">Zur Prüfung markiert</th>
                </tr>
              </thead>
              <tbody>
                {importResult.files.map((file) => (
                  <tr key={file.filename}>
                    <td>{file.filename}</td>
                    <td>{file.course_code}</td>
                    <td>{file.module_title}</td>
                    <td className="numeric">{file.row_count}</td>
                    <td className="numeric">{file.flagged_count}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {importResult.warnings.length > 0 ? (
              <ErrorList messages={importResult.warnings} title="Hinweise:" />
            ) : null}
          </div>
        ) : null}
      </div>

      {/* ------------------------------------------------------------------------- head count */}
      <div className="panel">
        <h2 style={{ marginTop: 0 }}>Kopfzahl für den Druck</h2>
        <p className="small muted">
          Zeigt, wie viele Klausurexemplare gedruckt werden müssen — ohne dafür die
          Anwesenheitsliste erzeugen zu müssen. Ausgeschlossene Studierende sind nicht enthalten.
        </p>
        {headCount === null ? (
          <p className="muted">Wird geladen …</p>
        ) : (
          <>
            <p data-testid="head-count-total">
              <strong>{headCount.total}</strong> angemeldete Studierende
            </p>
            {headCount.per_course.length > 0 ? (
              <table>
                <thead>
                  <tr>
                    <th scope="col">Studiengang</th>
                    <th scope="col">Anzahl</th>
                  </tr>
                </thead>
                <tbody>
                  {headCount.per_course.map((entry) => (
                    <tr key={entry.course_code} data-testid={`head-count-${entry.course_code}`}>
                      <td>{entry.course_code}</td>
                      <td className="numeric">{entry.count}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : null}
          </>
        )}
      </div>

      {/* --------------------------------------------------------------------- attendance list */}
      <div className="panel">
        <h2 style={{ marginTop: 0 }}>Anwesenheitsliste</h2>
        <p className="small muted">
          Zum Ausdrucken und handschriftlichen Abhaken der Anwesenheit vor der Klausur.
        </p>
        <fieldset style={{ border: "none", padding: 0, margin: "0 0 0.75rem" }}>
          <legend className="small muted" style={{ padding: 0 }}>
            Sortierung
          </legend>
          {ATTENDANCE_SORT_OPTIONS.map((option) => (
            <label
              key={option.value}
              htmlFor={`attendance-sort-${option.value}`}
              style={{ display: "block" }}
            >
              <input
                id={`attendance-sort-${option.value}`}
                type="radio"
                name="attendance-sort-order"
                value={option.value}
                checked={attendanceSortOrder === option.value}
                onChange={() => setAttendanceSortOrder(option.value)}
              />{" "}
              {option.label}
            </label>
          ))}
        </fieldset>
        <button
          type="button"
          onClick={() => void onDownloadAttendanceList()}
          disabled={downloading}
        >
          {downloading ? "Wird erstellt …" : "Anwesenheitsliste als PDF herunterladen"}
        </button>
        <div data-testid="download-errors">
          <ErrorList messages={downloadMessages} />
        </div>
      </div>

      {/* -------------------------------------------------------------------------- registrations */}
      <div className="panel">
        <div
          className="button-row"
          style={{ justifyContent: "space-between", marginBottom: "0.75rem" }}
        >
          <h2 style={{ marginTop: 0, marginBottom: 0 }}>Angemeldete Studierende</h2>
          <button
            type="button"
            className="danger"
            onClick={() => setConfirmingDeleteAll(true)}
            disabled={registrations.length === 0}
          >
            Alle entfernen
          </button>
        </div>
        <div className="row" style={{ marginBottom: "0.75rem" }}>
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
            <label htmlFor="show-excluded" style={{ display: "inline" }}>
              <input
                id="show-excluded"
                type="checkbox"
                checked={showExcluded}
                onChange={(event) => setShowExcluded(event.target.checked)}
              />{" "}
              Ausgeschlossene anzeigen
            </label>
          </div>
        </div>

        <div data-testid="list-errors">
          <ErrorList messages={listMessages} />
        </div>

        {loadingList ? (
          <p className="muted">Wird geladen …</p>
        ) : visibleRegistrations.length === 0 ? (
          <p className="muted">Keine Anmeldungen für diese Auswahl.</p>
        ) : (
          <table>
            <thead>
              <tr>
                <th scope="col">Status</th>
                <th scope="col">Studiengang</th>
                <th scope="col">Matr.-Nr.</th>
                <th scope="col">Nachname</th>
                <th scope="col">Vorname</th>
                <th scope="col">Vers.</th>
                <th scope="col">Kommentar</th>
                <th scope="col">Aktionen</th>
              </tr>
            </thead>
            <tbody>
              {visibleRegistrations.map((row) => {
                const rowClasses = [
                  row.flagged ? "row-flagged" : "",
                  row.excluded ? "row-excluded" : "",
                ]
                  .filter((value) => value !== "")
                  .join(" ");
                return (
                  <Fragment key={row.id}>
                    <tr className={rowClasses} data-testid={`registration-row-${row.id}`}>
                      <td>
                        {row.flagged ? (
                          <span className="badge badge-warn" data-testid={`flag-badge-${row.id}`}>
                            Zu prüfen
                          </span>
                        ) : null}
                        {row.excluded ? (
                          <span
                            className="badge badge-excluded"
                            data-testid={`excluded-badge-${row.id}`}
                          >
                            Ausgeschlossen
                          </span>
                        ) : null}
                      </td>
                      <td>{row.course_code}</td>
                      <td>{row.matrikelnummer}</td>
                      <td>{row.nachname}</td>
                      <td>{row.vorname}</td>
                      <td className="numeric">{row.versuch}</td>
                      <td>{row.kommentar ?? EMPTY_DISPLAY}</td>
                      <td>
                        <div className="button-row icon-button-row">
                          <button
                            type="button"
                            className="icon-button"
                            aria-label="Bearbeiten"
                            title="Bearbeiten"
                            onClick={() => onStartEdit(row)}
                          >
                            <IconEdit />
                          </button>
                          <button
                            type="button"
                            className="icon-button"
                            aria-label={row.excluded ? "Einschließen" : "Ausschließen"}
                            title={row.excluded ? "Einschließen" : "Ausschließen"}
                            onClick={() => void onToggleExcluded(row)}
                            disabled={togglingId === row.id}
                          >
                            {row.excluded ? <IconInclude /> : <IconExclude />}
                          </button>
                          <button
                            type="button"
                            className="danger icon-button"
                            aria-label="Löschen"
                            title="Löschen"
                            onClick={() => setPendingDeleteId(row.id)}
                          >
                            <IconTrash />
                          </button>
                        </div>
                      </td>
                    </tr>
                    {editingId === row.id && editForm !== null ? (
                      <tr>
                        <td colSpan={8}>
                          <div className="panel" style={{ margin: "0.5rem 0" }}>
                            <div data-testid="edit-errors">
                              <ErrorList messages={editMessages} />
                            </div>
                            <div className="row">
                              <div>
                                <label htmlFor={`edit-matrikelnummer-${row.id}`}>Matr.-Nr.</label>
                                <input
                                  id={`edit-matrikelnummer-${row.id}`}
                                  className="medium"
                                  type="text"
                                  value={editForm.matrikelnummer}
                                  onChange={(event) =>
                                    setEditForm({ ...editForm, matrikelnummer: event.target.value })
                                  }
                                />
                              </div>
                              <div>
                                <label htmlFor={`edit-nachname-${row.id}`}>Nachname</label>
                                <input
                                  id={`edit-nachname-${row.id}`}
                                  className="medium"
                                  type="text"
                                  value={editForm.nachname}
                                  onChange={(event) =>
                                    setEditForm({ ...editForm, nachname: event.target.value })
                                  }
                                />
                              </div>
                              <div>
                                <label htmlFor={`edit-vorname-${row.id}`}>Vorname</label>
                                <input
                                  id={`edit-vorname-${row.id}`}
                                  className="medium"
                                  type="text"
                                  value={editForm.vorname}
                                  onChange={(event) =>
                                    setEditForm({ ...editForm, vorname: event.target.value })
                                  }
                                />
                              </div>
                              <div>
                                <label htmlFor={`edit-versuch-${row.id}`}>Versuch</label>
                                <input
                                  id={`edit-versuch-${row.id}`}
                                  className="narrow"
                                  type="text"
                                  inputMode="numeric"
                                  value={editForm.versuchText}
                                  onChange={(event) =>
                                    setEditForm({ ...editForm, versuchText: event.target.value })
                                  }
                                />
                              </div>
                            </div>
                            <div className="row">
                              <div style={{ flex: "1 1 100%" }}>
                                <label htmlFor={`edit-course-${row.id}`}>Studiengang</label>
                                <input
                                  id={`edit-course-${row.id}`}
                                  className="medium"
                                  type="text"
                                  value={editForm.courseCode}
                                  onChange={(event) =>
                                    setEditForm({ ...editForm, courseCode: event.target.value })
                                  }
                                />
                              </div>
                              <div style={{ flex: "1 1 100%" }}>
                                <label htmlFor={`edit-module-${row.id}`}>Modultitel</label>
                                <input
                                  id={`edit-module-${row.id}`}
                                  type="text"
                                  value={editForm.moduleTitle}
                                  onChange={(event) =>
                                    setEditForm({ ...editForm, moduleTitle: event.target.value })
                                  }
                                />
                              </div>
                            </div>
                            <div className="field">
                              <label htmlFor={`edit-kommentar-${row.id}`}>Kommentar</label>
                              <input
                                id={`edit-kommentar-${row.id}`}
                                type="text"
                                value={editForm.kommentar}
                                onChange={(event) =>
                                  setEditForm({ ...editForm, kommentar: event.target.value })
                                }
                              />
                            </div>
                            <div className="button-row">
                              <button
                                type="button"
                                className="primary"
                                disabled={savingEdit}
                                onClick={() => void onSaveEdit(row.id)}
                              >
                                {savingEdit ? "Wird gespeichert …" : "Speichern"}
                              </button>
                              <button type="button" onClick={onCancelEdit} disabled={savingEdit}>
                                Abbrechen
                              </button>
                            </div>
                          </div>
                        </td>
                      </tr>
                    ) : null}
                  </Fragment>
                );
              })}
            </tbody>
          </table>
        )}
      </div>

      {/* --------------------------------------------------------------------------- manual add */}
      <div className="panel">
        <h2 style={{ marginTop: 0 }}>Anmeldung manuell hinzufügen</h2>
        <p className="small muted">
          Für eine nachträgliche Anmeldung, die in keiner importierten PDF-Datei enthalten war.
        </p>
        <div data-testid="add-errors">
          <ErrorList messages={addMessages} />
        </div>
        <form onSubmit={(event) => void onAdd(event)}>
          <div className="row">
            <div>
              <label htmlFor="add-matrikelnummer">Matr.-Nr.</label>
              <input
                id="add-matrikelnummer"
                className="medium"
                type="text"
                value={addForm.matrikelnummer}
                onChange={(event) => setAddForm({ ...addForm, matrikelnummer: event.target.value })}
              />
            </div>
            <div>
              <label htmlFor="add-nachname">Nachname</label>
              <input
                id="add-nachname"
                className="medium"
                type="text"
                value={addForm.nachname}
                onChange={(event) => setAddForm({ ...addForm, nachname: event.target.value })}
              />
            </div>
            <div>
              <label htmlFor="add-vorname">Vorname</label>
              <input
                id="add-vorname"
                className="medium"
                type="text"
                value={addForm.vorname}
                onChange={(event) => setAddForm({ ...addForm, vorname: event.target.value })}
              />
            </div>
            <div>
              <label htmlFor="add-versuch">Versuch</label>
              <input
                id="add-versuch"
                className="narrow"
                type="text"
                inputMode="numeric"
                value={addForm.versuchText}
                onChange={(event) => setAddForm({ ...addForm, versuchText: event.target.value })}
              />
            </div>
          </div>
          <div className="row">
            <div style={{ flex: "1 1 100%" }}>
              <label htmlFor="add-course">Studiengang</label>
              <input
                id="add-course"
                className="medium"
                type="text"
                value={addForm.courseCode}
                onChange={(event) => setAddForm({ ...addForm, courseCode: event.target.value })}
              />
            </div>
            <div style={{ flex: "1 1 100%" }}>
              <label htmlFor="add-module">Modultitel</label>
              <input
                id="add-module"
                type="text"
                value={addForm.moduleTitle}
                onChange={(event) => setAddForm({ ...addForm, moduleTitle: event.target.value })}
              />
            </div>
          </div>
          <div className="field">
            <label htmlFor="add-kommentar">Kommentar (optional)</label>
            <input
              id="add-kommentar"
              type="text"
              value={addForm.kommentar}
              onChange={(event) => setAddForm({ ...addForm, kommentar: event.target.value })}
            />
          </div>
          <button type="submit" className="primary" disabled={adding}>
            {adding ? "Wird hinzugefügt …" : "Anmeldung hinzufügen"}
          </button>
        </form>
      </div>

      {pendingDeleteId !== null ? (
        <ConfirmDialog
          title="Anmeldung endgültig löschen?"
          confirmLabel="Endgültig löschen"
          busy={deleting}
          onCancel={() => setPendingDeleteId(null)}
          onConfirm={() => void onConfirmDelete()}
        >
          <p>
            Die Anmeldung von{" "}
            <strong>
              {pendingDeleteRow !== null
                ? `${pendingDeleteRow.vorname} ${pendingDeleteRow.nachname} (Matr.-Nr. ${pendingDeleteRow.matrikelnummer})`
                : "dieser Person"}
            </strong>{" "}
            wird unwiderruflich gelöscht.
          </p>
          <p>
            Das ist etwas anderes als „Ausschließen“: Ausschließen behält die Anmeldung zur
            Nachvollziehbarkeit in der Datenbank, Löschen entfernt sie und alle zugehörigen Daten
            vollständig. Nutzen Sie „Ausschließen“, wenn die Anmeldung nur nicht in
            Anwesenheitsliste, Punkteerfassung und Berichten erscheinen soll.
          </p>
        </ConfirmDialog>
      ) : null}

      {confirmingDeleteAll ? (
        <ConfirmDialog
          title="Alle Anmeldungen entfernen?"
          confirmLabel="Alle entfernen"
          busy={deletingAll}
          onCancel={() => setConfirmingDeleteAll(false)}
          onConfirm={() => void onConfirmDeleteAll()}
        >
          <p>
            {pluralize(registrations.length, "Anmeldung", "Anmeldungen")} werden unwiderruflich
            gelöscht — einschließlich aller bereits erfassten Anwesenheits- und Punkteeinträge.
            Das betrifft auch ausgeschlossene Anmeldungen und ist unabhängig von der aktuellen
            Studiengang-Auswahl. Diese Aktion kann nicht rückgängig gemacht werden.
          </p>
        </ConfirmDialog>
      ) : null}
    </section>
  );
}
