import { useState, type FormEvent } from "react";
import { Navigate, useLocation, useNavigate } from "react-router-dom";

import { errorMessages } from "../api/client";
import { ErrorList } from "../components/Messages";
import { useAuth } from "../auth/AuthContext";

export default function LoginPage(): JSX.Element {
  const { user, loading, login } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();

  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [messages, setMessages] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);

  const state = location.state as { from?: string } | null;
  const target = state?.from ?? "/";

  if (loading) return <p className="muted">Wird geladen …</p>;
  if (user !== null) return <Navigate to={target} replace />;

  async function onSubmit(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    setMessages([]);
    setBusy(true);
    try {
      await login(username, password);
      navigate(target, { replace: true });
    } catch (error) {
      // The server deliberately returns the same message for wrong credentials and a
      // deactivated account; it is shown verbatim so the UI cannot leak the difference.
      setMessages(errorMessages(error));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="centered-page">
      <h1>GradingHelper</h1>
      <p className="muted small">Bitte melden Sie sich an.</p>
      <ErrorList messages={messages} />
      <form onSubmit={(event) => void onSubmit(event)}>
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
            autoComplete="current-password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            required
          />
        </div>
        <button type="submit" className="primary" disabled={busy}>
          {busy ? "Wird angemeldet …" : "Anmelden"}
        </button>
      </form>
    </div>
  );
}
