import type { JSX, ReactNode } from "react";

/**
 * Renders German error messages. Server messages (the section 7.2 validation texts, the login
 * failure) are always shown verbatim — the backend owns their wording.
 */
export function ErrorList({
  messages,
  title,
}: {
  messages: readonly string[];
  title?: string;
}): JSX.Element | null {
  if (messages.length === 0) return null;
  return (
    <div className="errors" role="alert">
      {title !== undefined ? <strong>{title}</strong> : null}
      {messages.length === 1 && title === undefined ? (
        <span>{messages[0]}</span>
      ) : (
        <ul>
          {messages.map((message, index) => (
            <li key={`${index}-${message}`}>{message}</li>
          ))}
        </ul>
      )}
    </div>
  );
}

export function SuccessNotice({ children }: { children: ReactNode }): JSX.Element {
  return (
    <div className="notice ok" role="status">
      {children}
    </div>
  );
}
