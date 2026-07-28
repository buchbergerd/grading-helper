import { useCallback, useEffect, useState, type FormEvent } from "react";

import {
  createUser,
  errorMessages,
  listUsers,
  resetUserPassword,
  updateUser,
  type AdminUser,
} from "../api/client";
import { ErrorList, SuccessNotice } from "../components/Messages";
import { useAuth } from "../auth/AuthContext";
import { formatDate } from "../util/format";

export default function AdminUsersPage(): JSX.Element {
  const { user: currentUser } = useAuth();

  const [users, setUsers] = useState<AdminUser[]>([]);
  const [loading, setLoading] = useState(true);
  const [messages, setMessages] = useState<string[]>([]);
  const [notice, setNotice] = useState<string | null>(null);

  const [newUsername, setNewUsername] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [newIsAdmin, setNewIsAdmin] = useState(false);
  const [creating, setCreating] = useState(false);

  const [resetFor, setResetFor] = useState<number | null>(null);
  const [resetPassword, setResetPassword] = useState("");

  const reload = useCallback(async () => {
    setLoading(true);
    try {
      setUsers(await listUsers());
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
    setNotice(null);
    if (newUsername.trim() === "" || newPassword === "") {
      setMessages(["Bitte Benutzername und Passwort angeben."]);
      return;
    }
    setCreating(true);
    try {
      await createUser({
        username: newUsername.trim(),
        password: newPassword,
        is_admin: newIsAdmin,
      });
      setNewUsername("");
      setNewPassword("");
      setNewIsAdmin(false);
      setNotice("Das Konto wurde angelegt.");
      await reload();
    } catch (error) {
      setMessages(errorMessages(error));
    } finally {
      setCreating(false);
    }
  }

  async function onToggleActive(user: AdminUser): Promise<void> {
    setNotice(null);
    try {
      await updateUser(user.id, { is_active: !user.is_active });
      setNotice(
        user.is_active
          ? `Das Konto ${user.username} wurde deaktiviert; bestehende Sitzungen wurden beendet.`
          : `Das Konto ${user.username} wurde wieder aktiviert.`,
      );
      await reload();
    } catch (error) {
      setMessages(errorMessages(error));
    }
  }

  async function onResetPassword(event: FormEvent<HTMLFormElement>, user: AdminUser): Promise<void> {
    event.preventDefault();
    setNotice(null);
    if (resetPassword === "") {
      setMessages(["Bitte ein neues Passwort angeben."]);
      return;
    }
    try {
      await resetUserPassword(user.id, resetPassword);
      setResetFor(null);
      setResetPassword("");
      setNotice(
        `Das Passwort von ${user.username} wurde zurückgesetzt; bestehende Sitzungen wurden beendet.`,
      );
    } catch (error) {
      setMessages(errorMessages(error));
    }
  }

  return (
    <section>
      <h1>Benutzerverwaltung</h1>
      <p className="muted small">
        Die Benutzerverwaltung umfasst ausschließlich Konten. Klausurdaten anderer Lehrender sind
        hier bewusst nicht einsehbar.
      </p>

      <ErrorList messages={messages} />
      {notice !== null ? <SuccessNotice>{notice}</SuccessNotice> : null}

      {loading ? (
        <p className="muted">Wird geladen …</p>
      ) : (
        <table>
          <thead>
            <tr>
              <th scope="col">Benutzername</th>
              <th scope="col">Rolle</th>
              <th scope="col">Status</th>
              <th scope="col">Angelegt am</th>
              <th scope="col">Aktionen</th>
            </tr>
          </thead>
          <tbody>
            {users.map((user) => {
              const isSelf = currentUser !== null && currentUser.id === user.id;
              return (
                <tr key={user.id}>
                  <td>{user.username}</td>
                  <td>{user.is_admin ? "Administration" : "Lehrende/r"}</td>
                  <td>{user.is_active ? "aktiv" : "deaktiviert"}</td>
                  <td>{formatDate(user.created_at)}</td>
                  <td>
                    <div className="button-row">
                      <button
                        type="button"
                        onClick={() => void onToggleActive(user)}
                        disabled={isSelf}
                        title={
                          isSelf
                            ? "Das eigene Konto kann nicht deaktiviert werden."
                            : undefined
                        }
                      >
                        {user.is_active ? "Deaktivieren" : "Aktivieren"}
                      </button>
                      <button
                        type="button"
                        onClick={() => {
                          setResetFor(resetFor === user.id ? null : user.id);
                          setResetPassword("");
                        }}
                      >
                        Passwort zurücksetzen
                      </button>
                    </div>
                    {resetFor === user.id ? (
                      <form
                        className="row"
                        style={{ marginTop: "0.5rem" }}
                        onSubmit={(event) => void onResetPassword(event, user)}
                      >
                        <div>
                          <label htmlFor={`reset-${user.id}`}>Neues Passwort</label>
                          <input
                            id={`reset-${user.id}`}
                            className="medium"
                            type="password"
                            autoComplete="new-password"
                            value={resetPassword}
                            onChange={(event) => setResetPassword(event.target.value)}
                          />
                        </div>
                        <button type="submit" className="primary">
                          Zurücksetzen
                        </button>
                        <button type="button" onClick={() => setResetFor(null)}>
                          Abbrechen
                        </button>
                      </form>
                    ) : null}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}

      <form className="panel" onSubmit={(event) => void onCreate(event)}>
        <h2 style={{ marginTop: 0 }}>Neues Konto</h2>
        <div className="row">
          <div>
            <label htmlFor="new-username">Benutzername</label>
            <input
              id="new-username"
              className="medium"
              type="text"
              autoComplete="off"
              value={newUsername}
              onChange={(event) => setNewUsername(event.target.value)}
            />
          </div>
          <div>
            <label htmlFor="new-password">Passwort</label>
            <input
              id="new-password"
              className="medium"
              type="password"
              autoComplete="new-password"
              value={newPassword}
              onChange={(event) => setNewPassword(event.target.value)}
            />
          </div>
          <div>
            <label htmlFor="new-is-admin" style={{ display: "inline" }}>
              <input
                id="new-is-admin"
                type="checkbox"
                checked={newIsAdmin}
                onChange={(event) => setNewIsAdmin(event.target.checked)}
              />{" "}
              Administrationsrechte
            </label>
          </div>
          <button type="submit" className="primary" disabled={creating}>
            {creating ? "Wird angelegt …" : "Konto anlegen"}
          </button>
        </div>
      </form>
    </section>
  );
}
