import { useState, type FormEvent } from "react";

import { changePassword, errorMessages } from "../api/client";
import { ErrorList, SuccessNotice } from "../components/Messages";

export default function ChangePasswordPage(): JSX.Element {
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [repeat, setRepeat] = useState("");
  const [messages, setMessages] = useState<string[]>([]);
  const [done, setDone] = useState(false);
  const [busy, setBusy] = useState(false);

  async function onSubmit(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    setMessages([]);
    setDone(false);
    if (next !== repeat) {
      setMessages(["Die beiden neuen Passwörter stimmen nicht überein."]);
      return;
    }
    setBusy(true);
    try {
      await changePassword(current, next);
      setDone(true);
      setCurrent("");
      setNext("");
      setRepeat("");
    } catch (error) {
      setMessages(errorMessages(error));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section style={{ maxWidth: "24rem" }}>
      <h1>Passwort ändern</h1>
      <ErrorList messages={messages} />
      {done ? <SuccessNotice>Das Passwort wurde geändert.</SuccessNotice> : null}
      <form onSubmit={(event) => void onSubmit(event)}>
        <div className="field">
          <label htmlFor="current">Aktuelles Passwort</label>
          <input
            id="current"
            type="password"
            autoComplete="current-password"
            value={current}
            onChange={(event) => setCurrent(event.target.value)}
            required
          />
        </div>
        <div className="field">
          <label htmlFor="next">Neues Passwort</label>
          <input
            id="next"
            type="password"
            autoComplete="new-password"
            value={next}
            onChange={(event) => setNext(event.target.value)}
            required
          />
        </div>
        <div className="field">
          <label htmlFor="repeat">Neues Passwort wiederholen</label>
          <input
            id="repeat"
            type="password"
            autoComplete="new-password"
            value={repeat}
            onChange={(event) => setRepeat(event.target.value)}
            required
          />
        </div>
        <button type="submit" className="primary" disabled={busy}>
          {busy ? "Wird gespeichert …" : "Passwort ändern"}
        </button>
      </form>
    </section>
  );
}
