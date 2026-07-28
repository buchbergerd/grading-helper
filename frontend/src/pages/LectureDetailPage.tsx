import { useCallback, useEffect, useState, type FormEvent } from "react";
import { Link, useParams } from "react-router-dom";

import {
  createExam,
  errorMessages,
  getLecture,
  type BonusMode,
  type LectureDetail,
} from "../api/client";
import { BONUS_MODE_OPTIONS } from "../grading/bonusMode";
import { ErrorList } from "../components/Messages";
import { formatDateOrDash, parseDateInput } from "../util/format";
import { parseRouteId } from "../util/id";

export default function LectureDetailPage(): JSX.Element {
  const params = useParams();
  const lectureId = parseRouteId(params["lectureId"]);

  const [lecture, setLecture] = useState<LectureDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [messages, setMessages] = useState<string[]>([]);

  const [semester, setSemester] = useState("");
  const [termin, setTermin] = useState("1. Termin");
  const [examDate, setExamDate] = useState("");
  const [bonusMode, setBonusMode] = useState<BonusMode>("ALWAYS");
  const [creating, setCreating] = useState(false);

  const reload = useCallback(async () => {
    if (lectureId === null) {
      setMessages(["Ungültige Adresse."]);
      setLoading(false);
      return;
    }
    setLoading(true);
    try {
      setLecture(await getLecture(lectureId));
      setMessages([]);
    } catch (error) {
      setMessages(errorMessages(error));
    } finally {
      setLoading(false);
    }
  }, [lectureId]);

  useEffect(() => {
    void reload();
  }, [reload]);

  async function onCreateExam(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    if (lectureId === null) return;

    const problems: string[] = [];
    if (semester.trim() === "") problems.push("Bitte ein Semester angeben.");
    if (termin.trim() === "") problems.push("Bitte einen Termin angeben.");

    let isoDate: string | null = null;
    if (examDate.trim() !== "") {
      isoDate = parseDateInput(examDate);
      if (isoDate === null) problems.push("Das Klausurdatum muss im Format TT.MM.JJJJ angegeben werden.");
    }
    if (problems.length > 0) {
      setMessages(problems);
      return;
    }

    setCreating(true);
    try {
      // exercises/grading_schema are deliberately omitted: the server then copies them forward
      // from this lecture's most recent prior exam (section 4) as a one-time editable copy.
      await createExam(lectureId, {
        semester: semester.trim(),
        termin: termin.trim(),
        exam_date: isoDate,
        bonus_mode: bonusMode,
      });
      setSemester("");
      setExamDate("");
      await reload();
    } catch (error) {
      setMessages(errorMessages(error));
    } finally {
      setCreating(false);
    }
  }

  return (
    <section>
      <p className="breadcrumb">
        <Link to="/">Vorlesungen</Link> / {lecture?.name ?? "…"}
      </p>
      <h1>{lecture?.name ?? "Vorlesung"}</h1>
      <ErrorList messages={messages} />

      {loading ? (
        <p className="muted">Wird geladen …</p>
      ) : lecture === null ? null : (
        <>
          <h2>Klausuren</h2>
          {lecture.exams.length === 0 ? (
            <p className="muted">Für diese Vorlesung ist noch keine Klausur angelegt.</p>
          ) : (
            <table>
              <thead>
                <tr>
                  <th scope="col">Semester</th>
                  <th scope="col">Termin</th>
                  <th scope="col">Klausurdatum</th>
                  <th scope="col">Bonuspunkte</th>
                </tr>
              </thead>
              <tbody>
                {lecture.exams.map((exam) => (
                  <tr key={exam.id}>
                    <td>
                      <Link to={`/klausuren/${exam.id}`}>{exam.semester}</Link>
                    </td>
                    <td>{exam.termin}</td>
                    <td>{formatDateOrDash(exam.exam_date)}</td>
                    <td className="small">
                      {BONUS_MODE_OPTIONS.find((option) => option.value === exam.bonus_mode)?.label ??
                        exam.bonus_mode}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}

          <form className="panel" onSubmit={(event) => void onCreateExam(event)}>
            <h2 style={{ marginTop: 0 }}>Neue Klausur anlegen</h2>
            <p className="small muted">
              Aufgaben und Notenschlüssel werden beim Anlegen einmalig aus der zuletzt angelegten
              Klausur dieser Vorlesung übernommen und können danach frei bearbeitet werden.
            </p>
            <div className="row">
              <div>
                <label htmlFor="semester">Semester</label>
                <input
                  id="semester"
                  className="medium"
                  type="text"
                  value={semester}
                  onChange={(event) => setSemester(event.target.value)}
                  placeholder="z. B. WiSe 23/24"
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
                  placeholder="z. B. 1. Termin"
                />
              </div>
              <div>
                <label htmlFor="exam-date">Klausurdatum</label>
                <input
                  id="exam-date"
                  className="narrow"
                  type="text"
                  inputMode="numeric"
                  value={examDate}
                  onChange={(event) => setExamDate(event.target.value)}
                  placeholder="TT.MM.JJJJ"
                />
              </div>
            </div>
            <fieldset style={{ marginTop: "1rem" }}>
              <legend>Bonuspunkte</legend>
              {BONUS_MODE_OPTIONS.map((option) => (
                <div className="radio-option" key={option.value}>
                  <input
                    id={`new-bonus-${option.value}`}
                    type="radio"
                    name="new-bonus-mode"
                    value={option.value}
                    checked={bonusMode === option.value}
                    onChange={() => setBonusMode(option.value)}
                  />{" "}
                  <label htmlFor={`new-bonus-${option.value}`}>{option.label}</label>
                  <span className="explanation">{option.explanation}</span>
                </div>
              ))}
            </fieldset>
            <button type="submit" className="primary" disabled={creating}>
              {creating ? "Wird angelegt …" : "Klausur anlegen"}
            </button>
          </form>
        </>
      )}
    </section>
  );
}
