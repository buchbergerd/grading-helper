import { useEffect, useRef, type ReactNode } from "react";

/**
 * Blocking confirmation for destructive actions. Deleting a lecture or an exam cascades to
 * every exam, registration and grade below it (section 13) and cannot be undone, so the
 * dialog spells that out instead of asking a bare "Sind Sie sicher?".
 */
export function ConfirmDialog({
  title,
  confirmLabel,
  onConfirm,
  onCancel,
  busy,
  children,
}: {
  title: string;
  confirmLabel: string;
  onConfirm: () => void;
  onCancel: () => void;
  busy?: boolean;
  children: ReactNode;
}): JSX.Element {
  const cancelRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    cancelRef.current?.focus();
  }, []);

  useEffect(() => {
    function onKeyDown(event: KeyboardEvent): void {
      if (event.key === "Escape") onCancel();
    }
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [onCancel]);

  return (
    <div className="dialog-backdrop">
      <div className="dialog" role="alertdialog" aria-modal="true" aria-label={title}>
        <h2 style={{ marginTop: 0 }}>{title}</h2>
        {children}
        <div className="button-row">
          <button type="button" ref={cancelRef} onClick={onCancel} disabled={busy === true}>
            Abbrechen
          </button>
          <button type="button" className="danger" onClick={onConfirm} disabled={busy === true}>
            {busy === true ? "Wird gelöscht …" : confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}
