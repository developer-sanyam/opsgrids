import { useRevealedField } from "@opsgrids/client";
import type { RevealHeaders, RevealSubject, UseStepUpResult } from "@opsgrids/client";
import { RevealableField } from "@opsgrids/ui";

import { USE_FIXTURES } from "@/api/client";

const NOUN = { phone: "phone number", email: "email address" } as const;

/** What fixture mode hands `load` in place of a step-up token: the fixture reveals read no header. */
const NO_STEP_UP: RevealHeaders = { Authorization: "" };

interface RevealFieldProps {
  kind: "phone" | "email";
  /** The masked form the record carries. `null` or blank: nothing is sealed. */
  masked: string | null;
  subject: RevealSubject;
  /** Presentation only: without it the field is the masked line, and the server refuses anyway. */
  canReveal: boolean;
  /** The screen's one `useStepUp()`; the screen renders its `<StepUpDialog />`. */
  stepUp: UseStepUpResult;
  /** The record's id. Another record hides the value and drops the code's access. */
  resetKey: string;
  load: (headers: RevealHeaders) => Promise<string | null>;
}

/**
 * One sealed CRM value: the shared `useRevealedField` hook and the shared `RevealableField`,
 * wired together. Fixture mode has no auth service to step up against, so there the reveal calls
 * `load` directly (shortcut: goes with fixtures mode in R3.4).
 */
export function RevealField({ kind, masked, subject, canReveal, stepUp, resetKey, load }: RevealFieldProps) {
  const field = useRevealedField({
    resetKey,
    stepUp: USE_FIXTURES ? { ...stepUp, run: (reveal) => reveal(NO_STEP_UP) } : stepUp,
    subject,
    noun: NOUN[kind],
    load,
  });
  // The button row plus the line that says "Hides again in 30 seconds" appears under it on reveal;
  // reserving that height here keeps the rows below from moving when it does.
  return (
    <div className={canReveal && masked !== null && masked.trim() !== "" ? "min-h-16" : undefined}>
      <RevealableField kind={kind} masked={masked} canReveal={canReveal} field={field} stepUpIdle={stepUp.state === "idle"} />
    </div>
  );
}
