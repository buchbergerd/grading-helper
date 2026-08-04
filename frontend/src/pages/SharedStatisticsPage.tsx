import { useCallback, useEffect, useState, type JSX } from "react";
import { Link, useParams } from "react-router";

import { errorMessages, getSharedStatistics, type ExamStatistics } from "../api/client";
import { StatisticsDashboard } from "../statistics/StatisticsDashboard";
import { useBonusSimulation } from "../statistics/useBonusSimulation";
import { ErrorList } from "../components/Messages";

/**
 * The unauthenticated view behind a §3 share link (`GET /api/public/statistics/{token}`) — no
 * session, no cookie, no `<AuthProvider>`/`RequireAuth` involved at all (see `App.tsx`, this
 * route sits outside the authenticated `<Layout>`). Renders exactly `StatisticsDashboard`, the
 * same component `ExamStatisticsPage` uses, and **nothing else**: no breadcrumb into the
 * authenticated app (those links would just bounce an anonymous visitor to `/login`), no
 * "Berichte" panel — the §10/§11 downloads carry student names and are simply never wired up on
 * this page, not merely hidden.
 *
 * The bonus-simulation box defaults its field to `"0"` rather than the exam's real
 * `bonus_points`: that value isn't part of the public statistics payload (deliberately — see
 * `app/statistics.py::ExamStatistics`, which carries no more than this page needs), and adding it
 * just to seed a default wouldn't be worth widening what the payload exposes.
 */
export default function SharedStatisticsPage(): JSX.Element {
  const params = useParams();
  const token = params["token"] ?? "";

  const [stats, setStats] = useState<ExamStatistics | null>(null);
  const [loading, setLoading] = useState(true);
  const [messages, setMessages] = useState<string[]>([]);

  const fetchStats = useCallback(
    (bonusPointsOverride?: string) => getSharedStatistics(token, bonusPointsOverride),
    [token],
  );
  const simulation = useBonusSimulation(fetchStats, "0");

  const reload = useCallback(async () => {
    if (token === "") {
      setMessages(["Ungültiger Link."]);
      setLoading(false);
      return;
    }
    setLoading(true);
    try {
      setStats(await getSharedStatistics(token));
      setMessages([]);
    } catch (error) {
      setMessages(errorMessages(error));
    } finally {
      setLoading(false);
    }
  }, [token]);

  useEffect(() => {
    void reload();
  }, [reload]);

  return (
    <>
      <header className="app-header">
        <div className="app-header-inner">
          <span className="app-brand">GradingHelper</span>
        </div>
      </header>
      <main className="app-main">
        <section>
          {loading ? (
            <p className="muted">Wird geladen …</p>
          ) : stats === null ? (
            <>
              <ErrorList messages={messages} />
              <p>
                <Link to="/">Zur Anmeldung</Link>
              </p>
            </>
          ) : (
            <>
              <h1>Statistik — {stats.lecture_name}</h1>
              <p className="muted small">
                {stats.semester}, {stats.termin}
                {/* Already `DD.MM.YYYY` off the wire (§14 #6) — no formatDateOrDash here. */}
                {stats.exam_date !== null ? ` — Klausurdatum ${stats.exam_date}` : ""}
              </p>
              <p className="muted small">
                Geteilte, schreibgeschützte Ansicht — Anmeldung nicht erforderlich.
              </p>
              <StatisticsDashboard stats={stats} simulation={simulation} />
            </>
          )}
        </section>
      </main>
    </>
  );
}
