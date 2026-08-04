import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type JSX,
  type ReactNode,
} from "react";
import { Navigate, useLocation } from "react-router";

import {
  ApiError,
  login as apiLogin,
  logout as apiLogout,
  me as apiMe,
  register as apiRegister,
  type User,
} from "../api/client";

interface AuthContextValue {
  /** null = definitely not logged in (once `loading` is false). */
  user: User | null;
  /** True until the initial /api/auth/me round trip has settled. */
  loading: boolean;
  login: (username: string, password: string) => Promise<void>;
  /** Self-service account creation via an invitation code (§3); also logs in. */
  register: (code: string, username: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
  refresh: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }): JSX.Element {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    try {
      setUser(await apiMe());
    } catch (error) {
      // A 401 from /api/auth/me is the normal "no session" answer, not a failure: it happens
      // on every first visit and after the 24 h sliding expiry. It must never surface as an error
      // message. Anything else is a real problem, but there is nothing useful the user can do
      // about it here either, so we still fall back to "logged out" and let the login page
      // report the next failure.
      if (!(error instanceof ApiError) || !error.isUnauthorized) {
        console.warn("Sitzungsstatus konnte nicht geprüft werden", error);
      }
      setUser(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const login = useCallback(async (username: string, password: string) => {
    // Let ApiError propagate: LoginPage shows the server's German message verbatim.
    setUser(await apiLogin(username, password));
  }, []);

  const register = useCallback(async (code: string, username: string, password: string) => {
    // Let ApiError propagate: RegisterPage shows the server's German message verbatim.
    setUser(await apiRegister(code, username, password));
  }, []);

  const logout = useCallback(async () => {
    try {
      await apiLogout();
    } finally {
      setUser(null);
    }
  }, []);

  const value = useMemo<AuthContextValue>(
    () => ({ user, loading, login, register, logout, refresh }),
    [user, loading, login, register, logout, refresh],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const context = useContext(AuthContext);
  if (context === null) {
    throw new Error("useAuth muss innerhalb von <AuthProvider> verwendet werden.");
  }
  return context;
}

function Pending(): JSX.Element {
  return <p className="muted">Wird geladen …</p>;
}

/** Redirects to /login when there is no session, remembering where the user wanted to go. */
export function RequireAuth({ children }: { children: ReactNode }): JSX.Element {
  const { user, loading } = useAuth();
  const location = useLocation();

  if (loading) return <Pending />;
  if (user === null) return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  return <>{children}</>;
}

/**
 * Admin-only wrapper. A logged-in non-admin gets an explanation rather than a redirect —
 * being sent back to the lecture list would look like a broken link. Note this is only a UI
 * guard; the API enforces the 403 itself (§3, §14 #5).
 */
export function RequireAdmin({ children }: { children: ReactNode }): JSX.Element {
  const { user, loading } = useAuth();
  const location = useLocation();

  if (loading) return <Pending />;
  if (user === null) return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  if (!user.is_admin) {
    return (
      <section>
        <h1>Kein Zugriff</h1>
        <p>Diese Seite ist der Benutzerverwaltung vorbehalten.</p>
      </section>
    );
  }
  return <>{children}</>;
}
