import type { JSX } from "react";

/**
 * `__APP_VERSION__` is injected by `vite.config.ts` at build time from `package.json`'s
 * `version` — baked into the bundle rather than fetched, so it shows on every page (including
 * `/login`, before a session exists) with no extra request.
 */
export default function Footer(): JSX.Element {
  return (
    <footer className="app-footer">
      <div className="app-footer-inner">Version {__APP_VERSION__}</div>
    </footer>
  );
}
