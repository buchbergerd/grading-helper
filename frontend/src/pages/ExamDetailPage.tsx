import { useCallback, useEffect, useState, type FormEvent } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";

import {
  deleteExam,
  errorMessages,
  getExam,
  updateExam,
  type BonusMode,
  type ExamDetail,
  type Exercise,
  type GradingSchemaRow,
} from "../api/client";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { ErrorList, SuccessNotice } from "../components/Messages";
import { BONUS_MODE_OPTIONS } from "../grading/bonusMode";
import {
  GRADE_SCALE,
  SCHWELLE_PREVIEW_HINT,
  sumMaxPoints,
  thresholdPointsPreview,
  validateExercises,
  validateGradingSchema,
} from "../grading/preview";
import {
  EMPTY_DISPLAY,
  formatDate,
  formatDecimal,
  parseDateInput,
  parseDecimalInput,
} from "../util/format";
import { parseRouteId } from "../util/id";

/**
 * Editor row for an exercise. `max_points` is the raw *text* the instructor typed, in whatever
 * German or dot form; it is converted to the canonical dot-decimal string only on save, and
 * never to a JS number. Values loaded from the API arrive as canonical decimals and are put
 * into the input verbatim, so "12.50" keeps its trailing zero.
 */
interface ExerciseRow {
  key: string;
  id?: number;
  name: string;
  maxPointsText: string;
}

interface SchemaRow {
  grade: string;
  percentageText: string;
}

let rowCounter = 0;
function nextKey(): string {
  rowCounter += 1;
  return `row-${rowCounter}`;
}

function toExerciseRows(exercises: readonly Exercise[]): ExerciseRow[] {
  return [...exercises]
    .sort((a, b) => a.position - b.position)
    .map((exercise) => ({
      key: nextKey(),
      ...(exercise.id === undefined ? {} : { id: exercise.id }),
      name: exercise.name,
      // Straight string assignment — no Number(), so trailing zeros survive into the field.
      maxPointsText: exercise.max_points,
    }));
}

/**
 * The schema editor always shows all ten grades of §7.1. An exam that has no schema yet (a
 * first exam in a lecture) starts with empty percentage fields rather than invented defaults —
 * an invented schema would silently become the one that grades the exam.
 */
function toSchemaRows(schema: readonly GradingSchemaRow[]): SchemaRow[] {
  return GRADE_SCALE.map((grade) => {
    const existing = schema.find((row) => row.grade === grade);
    return { grade, percentageText: existing?.percentage ?? "" };
  });
}

export default function ExamDetailPage(): JSX.Element {
  const params = useParams();
  const examId = parseRouteId(params["examId"]);
  const navigate = useNavigate();

  const [exam, setExam] = useState<ExamDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [messages, setMessages] = useState<string[]>([]);
  const [saved, setSaved] = useState(false);
  const [saving, setSaving] = useState(false);
  const [pendingDelete, setPendingDelete] = useState(false);
  const [deleting, setDeleting] = useState(false);

  const [semester, setSemester] = useState("");
  const [termin, setTermin] = useState("");
  const [examDateText, setExamDateText] = useState("");
  const [bonusMode, setBonusMode] = useState<BonusMode>("ALWAYS");
  const [exercises, setExercises] = useState<ExerciseRow[]>([]);
  const [schema, setSchema] = useState<SchemaRow[]>(toSchemaRows([]));

  const applyExam = useCallback((detail: ExamDetail) => {
    setExam(detail);
    setSemester(detail.semester);
    setTermin(detail.termin);
    setExamDateText(formatDate(detail.exam_date));
    setBonusMode(detail.bonus_mode);
    setExercises(toExerciseRows(detail.exercises));
    setSchema(toSchemaRows(detail.grading_schema));
  }, []);

  const reload = useCallback(async () => {
    if (examId === null) {
      setMessages(["Ungültige Adresse."]);
      setLoading(false);
      return;
    }
    setLoading(true);
    try {
      applyExam(await getExam(examId));
      setMessages([]);
    } catch (error) {
      setMessages(errorMessages(error));
    } finally {
      setLoading(false);
    }
  }, [examId, applyExam]);

  useEffect(() => {
    void reload();
  }, [reload]);

  /* ------------------------------------------------------- exercise editing */

  function updateExercise(key: string, patch: Partial<ExerciseRow>): void {
    setExercises((rows) => rows.map((row) => (row.key === key ? { ...row, ...patch } : row)));
  }

  function addExercise(): void {
    setExercises((rows) => [
      ...rows,
      { key: nextKey(), name: `Aufgabe ${rows.length + 1}`, maxPointsText: "" },
    ]);
  }

  function removeExercise(key: string): void {
    setExercises((rows) => rows.filter((row) => row.key !== key));
  }

  function moveExercise(index: number, delta: -1 | 1): void {
    setExercises((rows) => {
      const target = index + delta;
      if (target < 0 || target >= rows.length) return rows;
      const copy = [...rows];
      const a = copy[index];
      const b = copy[target];
      if (a === undefined || b === undefined) return rows;
      copy[index] = b;
      copy[target] = a;
      return copy;
    });
  }

  function updateSchema(grade: string, percentageText: string): void {
    setSchema((rows) =>
      rows.map((row) => (row.grade === grade ? { ...row, percentageText } : row)),
    );
  }

  /* --------------------------------------------------------------- preview */

  // Canonical decimal strings for the rows that currently parse. Rows still being typed are
  // dropped, which makes the total null and hides the threshold column rather than showing a
  // total that silently ignores an exercise.
  const parsedMaxPoints = exercises.map((row) => parseDecimalInput(row.maxPointsText));
  const allMaxPointsValid =
    exercises.length > 0 && parsedMaxPoints.every((value) => value !== null);
  const totalMaxPoints = allMaxPointsValid
    ? sumMaxPoints(parsedMaxPoints.filter((value): value is string => value !== null))
    : null;

  // True while the schema has not been configured at all (a first exam in a lecture).
  const schemaIsEmpty = schema.every((row) => row.percentageText.trim() === "");

  function thresholdFor(percentageText: string): string | null {
    if (totalMaxPoints === null) return null;
    const percentage = parseDecimalInput(percentageText);
    if (percentage === null) return null;
    return thresholdPointsPreview(percentage, totalMaxPoints);
  }

  /* ------------------------------------------------------------------ save */

  async function onSave(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    if (examId === null) return;
    setSaved(false);

    const problems: string[] = [];
    if (semester.trim() === "") problems.push("Bitte ein Semester angeben.");
    if (termin.trim() === "") problems.push("Bitte einen Termin angeben.");

    let isoDate: string | null = null;
    if (examDateText.trim() !== "") {
      isoDate = parseDateInput(examDateText);
      if (isoDate === null) {
        problems.push("Das Klausurdatum muss im Format TT.MM.JJJJ angegeben werden.");
      }
    }

    // Canonicalise every decimal: "12,5" -> "12.5". The digits are passed through unchanged,
    // so what is typed is exactly what the backend's Decimal(str) receives.
    const exercisePayload: Exercise[] = [];
    exercises.forEach((row, index) => {
      const canonical = parseDecimalInput(row.maxPointsText);
      exercisePayload.push({
        ...(row.id === undefined ? {} : { id: row.id }),
        name: row.name.trim(),
        max_points: canonical ?? row.maxPointsText,
        // 1-based, contiguous, in the order shown in the editor.
        position: index + 1,
      });
    });
    problems.push(...validateExercises(exercisePayload));

    const schemaPayload: GradingSchemaRow[] = schema.map((row) => {
      const canonical = parseDecimalInput(row.percentageText);
      return { grade: row.grade, percentage: canonical ?? row.percentageText };
    });
    // An exam whose lecture had no prior exam starts with an entirely empty schema. Saving is
    // still allowed then — otherwise the semester, the date and the exercises could not be
    // edited before all ten percentages exist — by leaving `grading_schema` out of the PATCH
    // altogether (the API only replaces the fields it is sent). A *partially* filled schema is
    // a real misconfiguration and is still rejected.
    if (!schemaIsEmpty) {
      // Client-side mirror of §7.2 for immediate feedback; the server validates again and its
      // German 422 messages are shown verbatim if they come back.
      problems.push(...validateGradingSchema(schemaPayload));
    }

    if (problems.length > 0) {
      setMessages(problems);
      return;
    }

    setSaving(true);
    try {
      applyExam(
        await updateExam(examId, {
          semester: semester.trim(),
          termin: termin.trim(),
          exam_date: isoDate,
          bonus_mode: bonusMode,
          // Full replace, not a merge — the contract is explicit about this.
          exercises: exercisePayload,
          ...(schemaIsEmpty ? {} : { grading_schema: schemaPayload }),
        }),
      );
      setMessages([]);
      setSaved(true);
    } catch (error) {
      setMessages(errorMessages(error));
    } finally {
      setSaving(false);
    }
  }

  async function onDeleteConfirmed(): Promise<void> {
    if (examId === null) return;
    setDeleting(true);
    try {
      await deleteExam(examId);
      const lectureId = exam?.lecture_id;
      navigate(lectureId === undefined ? "/" : `/vorlesungen/${lectureId}`, { replace: true });
    } catch (error) {
      setMessages(errorMessages(error));
      setPendingDelete(false);
    } finally {
      setDeleting(false);
    }
  }

  if (loading) return <p className="muted">Wird geladen …</p>;

  if (exam === null) {
    return (
      <section>
        <ErrorList messages={messages} />
        <Link to="/">Zur Vorlesungsübersicht</Link>
      </section>
    );
  }

  return (
    <section>
      <p className="breadcrumb">
        <Link to="/">Vorlesungen</Link> /{" "}
        <Link to={`/vorlesungen/${exam.lecture_id}`}>{exam.lecture_name}</Link> / {exam.semester}
      </p>
      <h1>
        {exam.lecture_name} — {exam.semester}, {exam.termin}
      </h1>
      <p className="muted small">
        {exam.registration_count === 1
          ? "1 angemeldete Studierende bzw. Studierender"
          : `${exam.registration_count} angemeldete Studierende`}
      </p>

      <ErrorList messages={messages} title={messages.length > 1 ? "Bitte prüfen:" : undefined} />
      {saved ? <SuccessNotice>Die Klausur wurde gespeichert.</SuccessNotice> : null}

      <form onSubmit={(event) => void onSave(event)}>
        <div className="panel">
          <h2 style={{ marginTop: 0 }}>Eckdaten</h2>
          <div className="row">
            <div>
              <label htmlFor="semester">Semester</label>
              <input
                id="semester"
                className="medium"
                type="text"
                value={semester}
                onChange={(event) => setSemester(event.target.value)}
              />
            </div>
            <div>
              <label htmlFor="termin">Termin</label>
              <input
                id="termin"
                className="medium"
                type="text"
                value={termin}
                onChange={(event) => setTermin(event.target.value)}
              />
            </div>
            <div>
              <label htmlFor="exam-date">Klausurdatum</label>
              <input
                id="exam-date"
                className="narrow"
                type="text"
                inputMode="numeric"
                placeholder="TT.MM.JJJJ"
                value={examDateText}
                onChange={(event) => setExamDateText(event.target.value)}
              />
            </div>
          </div>
        </div>

        <fieldset>
          <legend>Bonuspunkte</legend>
          {BONUS_MODE_OPTIONS.map((option) => (
            <div className="radio-option" key={option.value}>
              <input
                id={`bonus-${option.value}`}
                type="radio"
                name="bonus-mode"
                value={option.value}
                checked={bonusMode === option.value}
                onChange={() => setBonusMode(option.value)}
              />{" "}
              <label htmlFor={`bonus-${option.value}`}>{option.label}</label>
              <span className="explanation">{option.explanation}</span>
            </div>
          ))}
        </fieldset>

        <div className="panel">
          <h2 style={{ marginTop: 0 }}>Aufgaben</h2>
          {exercises.length === 0 ? (
            <p className="muted">Noch keine Aufgaben angelegt.</p>
          ) : (
            <table>
              <thead>
                <tr>
                  <th scope="col" style={{ width: "3rem" }}>
                    Nr.
                  </th>
                  <th scope="col">Bezeichnung</th>
                  <th scope="col" style={{ width: "10rem" }}>
                    Max. Punkte
                  </th>
                  <th scope="col" style={{ width: "12rem" }}>
                    Reihenfolge
                  </th>
                </tr>
              </thead>
              <tbody>
                {exercises.map((row, index) => (
                  <tr key={row.key}>
                    <td>{index + 1}</td>
                    <td>
                      <input
                        type="text"
                        aria-label={`Bezeichnung der Aufgabe ${index + 1}`}
                        value={row.name}
                        onChange={(event) => updateExercise(row.key, { name: event.target.value })}
                      />
                    </td>
                    <td>
                      {/*
                        type="text", never type="number": a number input hands back
                        valueAsNumber and normalises "12,50" — the trailing zero and the exact
                        decimal would be lost before the value ever reaches the API.
                      */}
                      <input
                        type="text"
                        inputMode="decimal"
                        className="narrow"
                        aria-label={`Maximale Punkte der Aufgabe ${index + 1}`}
                        value={row.maxPointsText}
                        onChange={(event) =>
                          updateExercise(row.key, { maxPointsText: event.target.value })
                        }
                      />
                    </td>
                    <td>
                      <div className="button-row">
                        <button
                          type="button"
                          onClick={() => moveExercise(index, -1)}
                          disabled={index === 0}
                          aria-label={`Aufgabe ${index + 1} nach oben`}
                        >
                          ↑
                        </button>
                        <button
                          type="button"
                          onClick={() => moveExercise(index, 1)}
                          disabled={index === exercises.length - 1}
                          aria-label={`Aufgabe ${index + 1} nach unten`}
                        >
                          ↓
                        </button>
                        <button
                          type="button"
                          className="danger"
                          onClick={() => removeExercise(row.key)}
                        >
                          Entfernen
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
              <tfoot>
                <tr>
                  <td colSpan={2}>Gesamtpunktzahl der Klausur</td>
                  <td data-testid="total-max-points">
                    {totalMaxPoints === null ? EMPTY_DISPLAY : formatDecimal(totalMaxPoints)}
                  </td>
                  <td />
                </tr>
              </tfoot>
            </table>
          )}
          <button type="button" onClick={addExercise}>
            Aufgabe hinzufügen
          </button>
        </div>

        <div className="panel">
          <h2 style={{ marginTop: 0 }}>Notenschlüssel</h2>
          <p className="small muted">
            Prozentwerte müssen streng fallend sein: jede bessere Note verlangt einen höheren
            Prozentsatz als die nächstschlechtere.
            {schemaIsEmpty
              ? " Der Notenschlüssel ist noch nicht festgelegt; er kann auch später ergänzt werden."
              : ""}
          </p>
          <table>
            <thead>
              <tr>
                <th scope="col" style={{ width: "6rem" }}>
                  Note
                </th>
                <th scope="col" style={{ width: "12rem" }}>
                  Prozent
                </th>
                <th scope="col">Benötigte Punkte (Vorschau)</th>
              </tr>
            </thead>
            <tbody>
              {schema.map((row) => {
                const threshold = thresholdFor(row.percentageText);
                return (
                  <tr key={row.grade}>
                    <th scope="row">{formatDecimal(row.grade)}</th>
                    <td>
                      <input
                        type="text"
                        inputMode="decimal"
                        className="narrow"
                        aria-label={`Prozentwert für Note ${formatDecimal(row.grade)}`}
                        value={row.percentageText}
                        onChange={(event) => updateSchema(row.grade, event.target.value)}
                      />
                    </td>
                    <td data-testid={`threshold-${row.grade}`}>
                      {threshold === null ? EMPTY_DISPLAY : formatDecimal(threshold)}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          <p className="preview-hint">{SCHWELLE_PREVIEW_HINT}</p>
        </div>

        <div className="button-row">
          <button type="submit" className="primary" disabled={saving}>
            {saving ? "Wird gespeichert …" : "Speichern"}
          </button>
          <button type="button" onClick={() => void reload()} disabled={saving}>
            Änderungen verwerfen
          </button>
          <button type="button" className="danger" onClick={() => setPendingDelete(true)}>
            Klausur löschen
          </button>
        </div>
      </form>

      {pendingDelete ? (
        <ConfirmDialog
          title="Klausur endgültig löschen?"
          confirmLabel="Endgültig löschen"
          busy={deleting}
          onCancel={() => setPendingDelete(false)}
          onConfirm={() => void onDeleteConfirmed()}
        >
          <p>
            Die Klausur <strong>{exam.semester}, {exam.termin}</strong> wird gelöscht — zusammen
            mit allen Anmeldungen, Punkten und Noten dieser Klausur.
          </p>
          <p>Dieser Vorgang kann nicht rückgängig gemacht werden.</p>
        </ConfirmDialog>
      ) : null}
    </section>
  );
}
