import type { ReactNode } from "react";

/**
 * The endpoint note every override form shows, so it is clear what submitting would do and which
 * service does it. Operator-facing documentation, not decoration: it is the difference between
 * "suspend" and "the activation is projected off and the customer's next call is a 402".
 */
export function DelegationNote({ children }: { children: ReactNode }) {
  return (
    <p className="rounded-tile bg-ground px-3 py-2.5 text-xs leading-relaxed text-muted">{children}</p>
  );
}

/** A path or an event name in the note, in the figure face. */
export function Code({ children }: { children: ReactNode }) {
  return <span className="money">{children}</span>;
}
