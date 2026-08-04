import { useState, type FormEvent, type JSX } from "react";
import { Navigate, useNavigate, useSearchParams } from "react-router";

import { errorMessages } from "../api/client";
import { ErrorList } from "../components/Messages";
import { useAuth } from "../auth/AuthContext";

/**
 * Self-service account creation via an admin-issued invitation code (§3).
 *
 * Reached either by pasting a code an admin handed over, or via a link the admin copied from
 * the invitation-codes panel (``/register?code=...``), which pre-fills the field below.
 */
export default function RegisterPage(): JSX.Element {
  const { user, loading, register } = useAuth();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();

  const [code, setCode] = useState(searchParams.get("code") ?? "");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [messages, setMessages] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);

  if (loading) return <p className="muted">Wird geladen …</p>;
  if (user !== null) return <Navigate to="/" replace />;

  async function onSubmit(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    setMessages([]);
    setBusy(true);
    try {
      await register(code, username, password);
      navigate("/", { replace: true });
    } catch (error) {
      // The server's German message is shown verbatim, whether the code, the username or the
      // password was the problem.
      setMessages(errorMessages(error));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="centered-page">
      <h1>GradingHelper</h1>
      <p className="muted small">
        Konto per Einladungscode erstellen. Den Code erhalten Sie von einer Administratorin oder
        einem Administrator.
      </p>
      <ErrorList messages={messages} />
      <form onSubmit={(event) => void onSubmit(event)}>
        <div className="field">
          <label htmlFor="code">Einladungscode</label>
          <input
            id="code"
            type="text"
            autoComplete="off"
            value={code}
            onChange={(event) => setCode(event.target.value)}
            required
          />
        </div>
        <div className="field">
          <label htmlFor="username">Benutzername</label>
          <input
            id="username"
            type="text"
            autoComplete="username"
            value={username}
            onChange={(event) => setUsername(event.target.value)}
            required
          />
        </div>
        <div className="field">
          <label htmlFor="password">Passwort</label>
          <input
            id="password"
            type="password"
            autoComplete="new-password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            required
          />
        </div>
        <button type="submit" className="primary" disabled={busy}>
          {busy ? "Konto wird erstellt …" : "Konto erstellen"}
        </button>
      </form>
    </div>
  );
}
