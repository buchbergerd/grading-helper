import { useCallback, useEffect, useRef, useState, type FormEvent, type JSX } from "react";

import {
  createInvitation,
  createUser,
  errorMessages,
  listInvitations,
  listUsers,
  resetUserPassword,
  revokeInvitation,
  updateUser,
  type AdminUser,
  type Invitation,
} from "../api/client";
import { ErrorList, SuccessNotice } from "../components/Messages";
import { IconMenu } from "../components/icons";
import { useAuth } from "../auth/AuthContext";
import { formatDate } from "../util/format";

const INVITATION_STATUS_LABEL: Record<Invitation["status"], string> = {
  active: "aktiv",
  revoked: "widerrufen",
  expired: "abgelaufen",
  exhausted: "ausgeschöpft",
};

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

  const [openMenuFor, setOpenMenuFor] = useState<number | null>(null);
  const [resetMode, setResetMode] = useState(false);
  const [resetPassword, setResetPassword] = useState("");
  const menuContainerRef = useRef<HTMLDivElement | null>(null);

  const [invitations, setInvitations] = useState<Invitation[]>([]);
  const [invitationsLoading, setInvitationsLoading] = useState(true);
  const [creatingInvitation, setCreatingInvitation] = useState(false);
  /** Empty string means unlimited — the field's placeholder/default state. */
  const [newInvitationMaxUses, setNewInvitationMaxUses] = useState("");

  const closeMenu = useCallback(() => {
    setOpenMenuFor(null);
    setResetMode(false);
    setResetPassword("");
  }, []);

  useEffect(() => {
    if (openMenuFor === null) return;
    function onPointerDown(event: MouseEvent): void {
      if (menuContainerRef.current && !menuContainerRef.current.contains(event.target as Node)) {
        closeMenu();
      }
    }
    function onKeyDown(event: KeyboardEvent): void {
      if (event.key === "Escape") closeMenu();
    }
    document.addEventListener("mousedown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("mousedown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [openMenuFor, closeMenu]);

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

  const reloadInvitations = useCallback(async () => {
    setInvitationsLoading(true);
    try {
      setInvitations(await listInvitations());
    } catch (error) {
      setMessages(errorMessages(error));
    } finally {
      setInvitationsLoading(false);
    }
  }, []);

  useEffect(() => {
    void reloadInvitations();
  }, [reloadInvitations]);

  async function onCreateInvitation(): Promise<void> {
    setNotice(null);
    const trimmed = newInvitationMaxUses.trim();
    const maxUses = trimmed === "" ? undefined : Number(trimmed);
    if (maxUses !== undefined && (!Number.isInteger(maxUses) || maxUses < 1)) {
      setMessages(["Die maximale Anzahl an Einlösungen muss eine ganze Zahl ab 1 sein."]);
      return;
    }
    setCreatingInvitation(true);
    try {
      await createInvitation(maxUses);
      setNotice("Der Einladungscode wurde erstellt.");
      setNewInvitationMaxUses("");
      await reloadInvitations();
    } catch (error) {
      setMessages(errorMessages(error));
    } finally {
      setCreatingInvitation(false);
    }
  }

  async function onCopyInvitationLink(code: string): Promise<void> {
    const link = `${window.location.origin}/register?code=${encodeURIComponent(code)}`;
    try {
      await navigator.clipboard.writeText(link);
      setNotice("Der Registrierungslink wurde in die Zwischenablage kopiert.");
    } catch {
      setMessages(["Der Link konnte nicht kopiert werden. Bitte den Code manuell weitergeben."]);
    }
  }

  async function onRevokeInvitation(invitation: Invitation): Promise<void> {
    setNotice(null);
    try {
      await revokeInvitation(invitation.id);
      setNotice("Der Einladungscode wurde widerrufen.");
      await reloadInvitations();
    } catch (error) {
      setMessages(errorMessages(error));
    }
  }

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

  async function onToggleAdmin(user: AdminUser): Promise<void> {
    setNotice(null);
    try {
      await updateUser(user.id, { is_admin: !user.is_admin });
      setNotice(
        user.is_admin
          ? `${user.username} hat keine Administrationsrechte mehr.`
          : `${user.username} hat jetzt Administrationsrechte.`,
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
      closeMenu();
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
                  <td className="actions-cell">
                    <div
                      className="actions-menu-container"
                      ref={openMenuFor === user.id ? menuContainerRef : undefined}
                    >
                      <button
                        type="button"
                        className="icon-button"
                        aria-haspopup="menu"
                        aria-expanded={openMenuFor === user.id}
                        aria-label={`Aktionen für ${user.username}`}
                        title="Aktionen"
                        onClick={() => {
                          if (openMenuFor === user.id) {
                            closeMenu();
                          } else {
                            setOpenMenuFor(user.id);
                            setResetMode(false);
                            setResetPassword("");
                          }
                        }}
                      >
                        <IconMenu />
                      </button>
                      {openMenuFor === user.id ? (
                        <div className="action-menu" role="menu">
                          {resetMode ? (
                            <form
                              className="action-menu-reset-form"
                              onSubmit={(event) => void onResetPassword(event, user)}
                            >
                              <div>
                                <label htmlFor={`reset-${user.id}`}>Neues Passwort</label>
                                <input
                                  id={`reset-${user.id}`}
                                  className="medium"
                                  type="password"
                                  autoComplete="new-password"
                                  autoFocus
                                  value={resetPassword}
                                  onChange={(event) => setResetPassword(event.target.value)}
                                />
                              </div>
                              <div className="button-row">
                                <button type="submit" className="primary">
                                  Zurücksetzen
                                </button>
                                <button type="button" onClick={closeMenu}>
                                  Abbrechen
                                </button>
                              </div>
                            </form>
                          ) : (
                            <>
                              <button
                                type="button"
                                role="menuitem"
                                className="action-menu-item"
                                disabled={isSelf}
                                title={
                                  isSelf
                                    ? "Das eigene Konto kann nicht deaktiviert werden."
                                    : undefined
                                }
                                onClick={() => {
                                  void onToggleActive(user);
                                  closeMenu();
                                }}
                              >
                                {user.is_active ? "Deaktivieren" : "Aktivieren"}
                              </button>
                              <button
                                type="button"
                                role="menuitem"
                                className="action-menu-item"
                                disabled={isSelf}
                                title={
                                  isSelf
                                    ? "Die eigenen Administratorrechte können nicht entzogen werden."
                                    : undefined
                                }
                                onClick={() => {
                                  void onToggleAdmin(user);
                                  closeMenu();
                                }}
                              >
                                {user.is_admin ? "Admin-Rechte entziehen" : "Admin-Rechte geben"}
                              </button>
                              <button
                                type="button"
                                role="menuitem"
                                className="action-menu-item"
                                onClick={() => {
                                  setResetMode(true);
                                  setResetPassword("");
                                }}
                              >
                                Passwort zurücksetzen
                              </button>
                            </>
                          )}
                        </div>
                      ) : null}
                    </div>
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

      <div className="panel">
        <h2 style={{ marginTop: 0 }}>Einladungscodes</h2>
        <p className="muted small">
          Alternative zum manuellen Anlegen: Mit einem Einladungscode kann sich eine neue Lehrkraft
          selbst ein Konto erstellen. Ein Code ist beliebig oft einlösbar (z. B. einmal im
          Team-Chat geteilt) und läuft automatisch ab (siehe „Gültig bis“) oder kann jederzeit
          widerrufen werden.
        </p>
        <div style={{ display: "flex", alignItems: "center", gap: "0.5rem" }}>
          <label htmlFor="new-invitation-max-uses" className="muted small">
            Max. Einlösungen (optional)
          </label>
          <input
            id="new-invitation-max-uses"
            type="text"
            inputMode="numeric"
            style={{ width: "5rem" }}
            placeholder="unbegrenzt"
            value={newInvitationMaxUses}
            onChange={(event) => setNewInvitationMaxUses(event.target.value)}
          />
          <button
            type="button"
            className="primary"
            disabled={creatingInvitation}
            onClick={() => void onCreateInvitation()}
          >
            {creatingInvitation ? "Wird erstellt …" : "Einladungscode erstellen"}
          </button>
        </div>

        {invitationsLoading ? (
          <p className="muted">Wird geladen …</p>
        ) : invitations.length === 0 ? (
          <p className="muted">Noch keine Einladungscodes erstellt.</p>
        ) : (
          <table>
            <thead>
              <tr>
                <th scope="col">Code</th>
                <th scope="col">Erstellt von</th>
                <th scope="col">Erstellt am</th>
                <th scope="col">Gültig bis</th>
                <th scope="col">Status</th>
                <th scope="col">Einlösungen</th>
                <th scope="col">Aktionen</th>
              </tr>
            </thead>
            <tbody>
              {invitations.map((invitation) => (
                <tr key={invitation.id}>
                  <td>
                    <code>{invitation.code}</code>
                  </td>
                  <td>{invitation.created_by}</td>
                  <td>{formatDate(invitation.created_at)}</td>
                  <td>{formatDate(invitation.expires_at)}</td>
                  <td>{INVITATION_STATUS_LABEL[invitation.status]}</td>
                  <td>
                    {invitation.max_uses === null
                      ? invitation.redemption_count
                      : `${invitation.redemption_count} / ${invitation.max_uses}`}
                  </td>
                  <td className="actions-cell">
                    <button type="button" onClick={() => void onCopyInvitationLink(invitation.code)}>
                      Link kopieren
                    </button>
                    {invitation.status === "active" ? (
                      <button type="button" onClick={() => void onRevokeInvitation(invitation)}>
                        Widerrufen
                      </button>
                    ) : null}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </section>
  );
}
