import { useCallback, useEffect, useRef, useState, type FormEvent, type JSX } from "react";
import { Link } from "react-router";

import {
  createLecture,
  deleteLecture,
  errorMessages,
  importExam,
  listLectures,
  updateLecture,
  type LectureSummary,
} from "../api/client";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { ErrorList, SuccessNotice } from "../components/Messages";
import { formatDate, pluralize } from "../util/format";

export default function LectureListPage(): JSX.Element {
  const [lectures, setLectures] = useState<LectureSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [messages, setMessages] = useState<string[]>([]);

  const [newName, setNewName] = useState("");
  const [creating, setCreating] = useState(false);

  const [editingId, setEditingId] = useState<number | null>(null);
  const [editName, setEditName] = useState("");

  const [pendingDelete, setPendingDelete] = useState<LectureSummary | null>(null);
  const [deleting, setDeleting] = useState(false);

  const [importing, setImporting] = useState(false);
  const [importMessages, setImportMessages] = useState<string[]>([]);
  const [importedExam, setImportedExam] = useState<{
    id: number;
    lectureName: string;
    lectureCreated: boolean;
  } | null>(null);
  const importFileInput = useRef<HTMLInputElement | null>(null);

  const reload = useCallback(async () => {
    setLoading(true);
    try {
      setLectures(await listLectures());
      setMessages([]);
    } catch (error) {
      setMessages(errorMessages(error));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void reload();
  }, [reload]);

  async function onCreate(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    const name = newName.trim();
    if (name === "") {
      setMessages(["Bitte einen Namen für die Vorlesung eingeben."]);
      return;
    }
    setCreating(true);
    try {
      await createLecture(name);
      setNewName("");
      await reload();
    } catch (error) {
      setMessages(errorMessages(error));
    } finally {
      setCreating(false);
    }
  }

  async function onRename(id: number): Promise<void> {
    const name = editName.trim();
    if (name === "") {
      setMessages(["Der Name der Vorlesung darf nicht leer sein."]);
      return;
    }
    try {
      await updateLecture(id, name);
      setEditingId(null);
      await reload();
    } catch (error) {
      setMessages(errorMessages(error));
    }
  }

  /**
   * Whole-exam import (backup/transfer, added post-milestone-6): always creates a new exam, and
   * auto-creates the lecture named in the file if the caller doesn't already have one by that
   * exact name (`ExamImportResult.lecture_created`). Stays on this page and reloads the list
   * (a new lecture may now exist) rather than navigating straight to the new exam, so the
   * lecture-created-or-reused notice is actually visible before the instructor moves on.
   */
  async function onImport(): Promise<void> {
    const file = importFileInput.current?.files?.[0];
    if (file === undefined) {
      setImportMessages(["Bitte zuerst eine Exportdatei auswählen."]);
      return;
    }
    setImporting(true);
    setImportMessages([]);
    setImportedExam(null);
    try {
      const result = await importExam(file);
      if (importFileInput.current !== null) importFileInput.current.value = "";
      setImportedExam({
        id: result.exam.id,
        lectureName: result.exam.lecture_name,
        lectureCreated: result.lecture_created,
      });
      await reload();
    } catch (error) {
      setImportMessages(errorMessages(error));
    } finally {
      setImporting(false);
    }
  }

  async function onDeleteConfirmed(): Promise<void> {
    if (pendingDelete === null) return;
    setDeleting(true);
    try {
      // The client always appends ?confirm=true — the API refuses with 409 otherwise,
      // precisely so a delete cannot happen without this dialog having been shown.
      await deleteLecture(pendingDelete.id);
      setPendingDelete(null);
      await reload();
    } catch (error) {
      setMessages(errorMessages(error));
    } finally {
      setDeleting(false);
    }
  }

  return (
    <section>
      <h1>Vorlesungen</h1>
      <ErrorList messages={messages} />

      <form className="panel" onSubmit={(event) => void onCreate(event)}>
        <h2 style={{ marginTop: 0 }}>Neue Vorlesung</h2>
        <div className="row">
          <div style={{ flex: "1 1 20rem" }}>
            <label htmlFor="new-lecture">Name der Vorlesung</label>
            <input
              id="new-lecture"
              type="text"
              value={newName}
              onChange={(event) => setNewName(event.target.value)}
              placeholder="z. B. Grundlagen der Informationstechnik"
            />
          </div>
          <button type="submit" className="primary" disabled={creating}>
            {creating ? "Wird angelegt …" : "Anlegen"}
          </button>
        </div>
      </form>

      <div className="panel">
        <h2 style={{ marginTop: 0 }}>Klausur importieren</h2>
        <p className="small muted">
          Aus einer zuvor exportierten Datei (Eckdaten, Aufgaben, Notenschlüssel, Anmeldungen und
          Punkte) eine neue Klausur anlegen. Gibt es noch keine Vorlesung mit dem in der Datei
          hinterlegten Namen, wird sie automatisch angelegt.
        </p>
        <div className="row">
          <div style={{ flex: "1 1 20rem" }}>
            <label htmlFor="import-file">Exportdatei</label>
            <input id="import-file" type="file" accept=".json,application/json" ref={importFileInput} />
          </div>
          <button type="button" className="primary" onClick={() => void onImport()} disabled={importing}>
            {importing ? "Wird importiert …" : "Importieren"}
          </button>
        </div>
        <ErrorList messages={importMessages} />
        {importedExam !== null ? (
          <SuccessNotice>
            {importedExam.lectureCreated
              ? `Die Klausur wurde importiert — eine neue Vorlesung „${importedExam.lectureName}“ wurde dafür angelegt.`
              : `Die Klausur wurde importiert und der Vorlesung „${importedExam.lectureName}“ zugeordnet.`}{" "}
            <Link to={`/klausuren/${importedExam.id}`}>Zur importierten Klausur</Link>
          </SuccessNotice>
        ) : null}
      </div>

      {loading ? (
        <p className="muted">Wird geladen …</p>
      ) : lectures.length === 0 ? (
        <p className="muted">Noch keine Vorlesungen angelegt.</p>
      ) : (
        <table>
          <caption className="small muted" style={{ captionSide: "bottom", textAlign: "left" }}>
            {pluralize(lectures.length, "Vorlesung", "Vorlesungen")}
          </caption>
          <thead>
            <tr>
              <th scope="col">Vorlesung</th>
              <th scope="col" className="numeric">
                Klausuren
              </th>
              <th scope="col">Angelegt am</th>
              <th scope="col">Aktionen</th>
            </tr>
          </thead>
          <tbody>
            {lectures.map((lecture) => (
              <tr key={lecture.id}>
                <td>
                  {editingId === lecture.id ? (
                    <input
                      type="text"
                      aria-label="Neuer Name der Vorlesung"
                      value={editName}
                      onChange={(event) => setEditName(event.target.value)}
                    />
                  ) : (
                    <Link to={`/vorlesungen/${lecture.id}`}>{lecture.name}</Link>
                  )}
                </td>
                <td className="numeric">{lecture.exam_count}</td>
                <td>{formatDate(lecture.created_at)}</td>
                <td>
                  <div className="button-row">
                    {editingId === lecture.id ? (
                      <>
                        <button
                          type="button"
                          className="primary"
                          onClick={() => void onRename(lecture.id)}
                        >
                          Speichern
                        </button>
                        <button type="button" onClick={() => setEditingId(null)}>
                          Abbrechen
                        </button>
                      </>
                    ) : (
                      <>
                        <button
                          type="button"
                          onClick={() => {
                            setEditingId(lecture.id);
                            setEditName(lecture.name);
                          }}
                        >
                          Umbenennen
                        </button>
                        <button
                          type="button"
                          className="danger"
                          onClick={() => setPendingDelete(lecture)}
                        >
                          Löschen
                        </button>
                      </>
                    )}
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {pendingDelete !== null ? (
        <ConfirmDialog
          title="Vorlesung endgültig löschen?"
          confirmLabel="Endgültig löschen"
          busy={deleting}
          onCancel={() => setPendingDelete(null)}
          onConfirm={() => void onDeleteConfirmed()}
        >
          <p>
            Die Vorlesung <strong>{pendingDelete.name}</strong> wird gelöscht — zusammen mit{" "}
            <strong>allen {pendingDelete.exam_count} Klausuren</strong> dieser Vorlesung sowie
            allen dazugehörigen Anmeldungen, Punkten und Noten.
          </p>
          <p>Dieser Vorgang kann nicht rückgängig gemacht werden.</p>
        </ConfirmDialog>
      ) : null}
    </section>
  );
}
