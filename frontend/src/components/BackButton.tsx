import type { MouseEvent } from "react";
import { Link } from "react-router-dom";

import { IconBack } from "./icons";

/**
 * A compact "one level up" button placed to the left of a page's breadcrumb (`<p
 * className="breadcrumb">`). Deliberately not `navigate(-1)`/browser history — that goes wherever
 * the user happened to arrive from, which is not "one level up" in the breadcrumb's own
 * hierarchy. Instead every page computes its own parent route (the breadcrumb's
 * second-to-last entry) and passes it as `to`.
 */
export function BackButton({
  to,
  onClick,
}: {
  /** The parent route, or `null`/`undefined` when there is no parent level yet (data still
   * loading) or none exists at all — renders nothing rather than a button that goes nowhere. */
  to?: string | null;
  /** Passed through to the underlying `Link`, e.g. PointsEntryPage's unsaved-changes guard,
   * which its other breadcrumb links already use. */
  onClick?: (event: MouseEvent<HTMLAnchorElement>) => void;
}): JSX.Element | null {
  if (to === null || to === undefined) return null;
  return (
    <Link
      to={to}
      className="back-button"
      aria-label="Zurück"
      title="Eine Ebene zurück"
      onClick={onClick}
    >
      <IconBack />
    </Link>
  );
}
