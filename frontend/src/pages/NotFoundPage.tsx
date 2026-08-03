import type { JSX } from "react";
import { Link } from "react-router";

export default function NotFoundPage(): JSX.Element {
  return (
    <div className="centered-page">
      <h1>Seite nicht gefunden</h1>
      <p className="muted">Diese Adresse gibt es nicht.</p>
      <Link to="/">Zur Vorlesungsübersicht</Link>
    </div>
  );
}
