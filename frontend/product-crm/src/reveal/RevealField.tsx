import { useRevealedField } from "@opsgrids/client";
import type { RevealSubject } from "@opsgrids/client";
import { RevealableField } from "@opsgrids/ui";

const NOUN = { phone: "phone number", email: "email address" } as const;

interface RevealFieldProps {
  kind: "phone" | "email";
  /** The masked form the record carries. `null` or blank: nothing is sealed. */
  masked: string | null;
  subject: RevealSubject;
  /** Presentation only: without it the field is the masked line, and the server refuses anyway. */
  canReveal: boolean;
  /** The record's id. Another record shows its own state; a revealed value sticks for the session. */
  resetKey: string;
  load: () => Promise<string | null>;
}

/**
 * One sealed CRM value: the shared `useRevealedField` hook and the shared `RevealableField`,
 * wired together. One tap reveals it, with no one-time code (ADR-0023 addendum, 2026-10-15), and
 * it stays visible for the session as a `tel:` / `mailto:` link.
 */
export function RevealField({ kind, masked, subject, canReveal, resetKey, load }: RevealFieldProps) {
  const field = useRevealedField({ resetKey, subject, noun: NOUN[kind], load });
  // The row keeps its height whether it shows the masked line with its button or the value's
  // 44px link, so the rows below do not move when a reveal lands.
  return (
    <div className={canReveal && masked !== null && masked.trim() !== "" ? "min-h-11" : undefined}>
      <RevealableField kind={kind} masked={masked} canReveal={canReveal} field={field} />
    </div>
  );
}
