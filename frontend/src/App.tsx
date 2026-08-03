import type { JSX } from "react";
import { Link, NavLink, Outlet, Route, Routes, useNavigate } from "react-router";

import { RequireAdmin, RequireAuth, useAuth } from "./auth/AuthContext";
import AdminUsersPage from "./pages/AdminUsersPage";
import ChangePasswordPage from "./pages/ChangePasswordPage";
import ExamDetailPage from "./pages/ExamDetailPage";
import ExamStatisticsPage from "./pages/ExamStatisticsPage";
import LectureDetailPage from "./pages/LectureDetailPage";
import LectureListPage from "./pages/LectureListPage";
import LoginPage from "./pages/LoginPage";
import NotFoundPage from "./pages/NotFoundPage";
import PointsEntryPage from "./pages/PointsEntryPage";
import RegistrationsPage from "./pages/RegistrationsPage";

function Layout(): JSX.Element {
  const { user, logout } = useAuth();
  const navigate = useNavigate();

  async function onLogout(): Promise<void> {
    await logout();
    navigate("/login", { replace: true });
  }

  return (
    <>
      <header className="app-header">
        <div className="app-header-inner">
          <Link className="app-brand" to="/">
            GradingHelper
          </Link>
          <nav className="app-nav" aria-label="Hauptnavigation">
            <NavLink to="/">Vorlesungen</NavLink>
            {user?.is_admin === true ? <NavLink to="/admin/benutzer">Benutzer</NavLink> : null}
          </nav>
          <div className="app-user">
            {user !== null ? <span>Angemeldet als {user.username}</span> : null}
            <Link to="/passwort">Passwort ändern</Link>
            <button type="button" className="link" onClick={() => void onLogout()}>
              Abmelden
            </button>
          </div>
        </div>
      </header>
      <main className="app-main">
        <Outlet />
      </main>
    </>
  );
}

export default function App(): JSX.Element {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route
        element={
          <RequireAuth>
            <Layout />
          </RequireAuth>
        }
      >
        <Route path="/" element={<LectureListPage />} />
        <Route path="/vorlesungen/:lectureId" element={<LectureDetailPage />} />
        <Route path="/klausuren/:examId" element={<ExamDetailPage />} />
        <Route path="/klausuren/:examId/anmeldungen" element={<RegistrationsPage />} />
        <Route path="/klausuren/:examId/punkte" element={<PointsEntryPage />} />
        <Route path="/klausuren/:examId/statistik" element={<ExamStatisticsPage />} />
        <Route path="/passwort" element={<ChangePasswordPage />} />
        <Route
          path="/admin/benutzer"
          element={
            <RequireAdmin>
              <AdminUsersPage />
            </RequireAdmin>
          }
        />
      </Route>
      <Route path="*" element={<NotFoundPage />} />
    </Routes>
  );
}
