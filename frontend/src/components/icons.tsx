/**
 * Small inline SVG icons for row actions. Deliberately hand-rolled instead of an icon-library
 * dependency: the app must build fully offline (§13) and no icon package is installed.
 *
 * Every icon is purely decorative — `aria-hidden`/`focusable="false"` so it never contributes
 * its own accessible name. The button that wraps it carries the real `aria-label`/`title`; an
 * inner `<title>` here would give the button a doubled/ambiguous accessible name.
 */

const commonProps = {
  width: 16,
  height: 16,
  viewBox: "0 0 16 16",
  fill: "none",
  stroke: "currentColor",
  strokeWidth: 1.5,
  strokeLinecap: "round" as const,
  strokeLinejoin: "round" as const,
  "aria-hidden": true,
  focusable: false,
};

/** Pencil — "Bearbeiten". */
export function IconEdit(): JSX.Element {
  return (
    <svg {...commonProps}>
      <path d="M10.5 2.5 13.5 5.5 5 14H2v-3z" />
      <path d="M9 4l3 3" />
    </svg>
  );
}

/** Slashed circle ("no entry") — "Ausschließen". */
export function IconExclude(): JSX.Element {
  return (
    <svg {...commonProps}>
      <circle cx="8" cy="8" r="6" />
      <path d="M4 4l8 8" />
    </svg>
  );
}

/** Circle with a checkmark — "Einschließen" (undoing an exclude), visually distinct from the
 * slashed circle above so the toggled state is obvious at a glance, not just via the tooltip. */
export function IconInclude(): JSX.Element {
  return (
    <svg {...commonProps}>
      <circle cx="8" cy="8" r="6" />
      <path d="M5.2 8.2 7.1 10l3.7-4" />
    </svg>
  );
}

/** Trash can — "Löschen". */
export function IconTrash(): JSX.Element {
  return (
    <svg {...commonProps}>
      <path d="M3 4.5h10" />
      <path d="M5.5 4.5V3a1 1 0 0 1 1-1h3a1 1 0 0 1 1 1v1.5" />
      <path d="M4.5 4.5 5 13a1 1 0 0 0 1 .9h4a1 1 0 0 0 1-.9l.5-8.5" />
      <path d="M6.7 7v4.2" />
      <path d="M9.3 7v4.2" />
    </svg>
  );
}

/** Left chevron — "Zurück" (`BackButton`, one level up in a breadcrumb). */
export function IconBack(): JSX.Element {
  return (
    <svg {...commonProps}>
      <path d="M10 3 5 8l5 5" />
    </svg>
  );
}
