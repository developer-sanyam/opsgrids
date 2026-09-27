import { useEffect, useId, useRef } from "react";
import type { ReactNode } from "react";

import { Button } from "./Button";
import { Dialog } from "./Dialog";
import { StepUpCodeForm } from "./StepUpDialogCodeForm";
import type { StepUpDialogError } from "./StepUpDialogCodeForm";

export type { StepUpDialogError } from "./StepUpDialogCodeForm";

/**
 * Whether a code can help at all. `ready` asks for one; the others are the server saying no code
 * ever will, and why: no second factor is enrolled, the session is a single sign-on one, or the
 * authenticator was added after this session began and the person has to sign in again.
 */
export type StepUpMfaState = "ready" | "not-enrolled" | "sso" | "reauthenticate";

export interface StepUpDialogProps {
  open: boolean;
  /** A code is with the server. The field is read-only, Verify says so, and closing is refused. */
  busy: boolean;
  /** What the last attempt said. Cleared by the host when the next attempt starts. */
  error?: StepUpDialogError | undefined;
  mfaState: StepUpMfaState;
  /** Six digits, nothing else — called on the sixth digit, on Enter and on Verify. */
  onSubmit: (code: string) => void;
  /** Cancel, Escape and the close control. Never called while `busy`. */
  onCancel: () => void;
  /** The dashboard's Security page — where the not-enrolled state sends someone to set up an app. */
  securityHref: string;
}

const COPY = {
  title: "Confirm it's you",
  prompt:
    "Enter the 6-digit code from your authenticator app to reveal this. Codes can be used once: if you just signed in, wait for the next one.",
  cancel: "Cancel",
  verify: "Verify",
  verifying: "Verifying…",
  close: "Close",
  sso: "Revealing this is unavailable for single sign-on accounts.",
  reauthenticate:
    "Your authenticator was set up after you signed in. Sign out and sign in again, then try once more.",
  notEnrolled: {
    before: "Two-factor authentication is required to reveal this, and it isn't set up on your account yet. Set it up on the ",
    link: "Security page",
    linkHint: " (opens in a new tab)",
    after: ", then come back and try again.",
  },
};

/**
 * The prompt for a fresh one-time code before something dangerous — revealing a masked phone
 * number, unmasking an ID. Presentational: `useStepUp()` in `@opsgrids/client` owns the request
 * and hands over exactly these props (`<StepUpDialog {...stepUp.dialogProps} />`).
 *
 * **Four ways in, one dialog.** `ready` is the code field. `not-enrolled`, `sso` and
 * `reauthenticate` replace it with the reason no code can work, rather than letting someone type
 * into a field that will refuse everything; when the answer arrives with the dialog already open,
 * focus moves to the one thing to do next (the link, or Close) and the new sentence is read aloud.
 *
 * **Not dismissable mid-request.** Escape, Cancel and the close control are inert while `busy`,
 * the same rule `ConfirmDialog` keeps — the code is already gone, and a dialog that vanishes
 * leaves the person not knowing whether it counted.
 *
 * Focus lands on the code field, and returns to the control that opened the dialog on close
 * (`Dialog`'s trap does both). This component gates nothing: the reveal endpoint checks the token.
 */
export function StepUpDialog({
  open,
  busy,
  error,
  mfaState,
  onSubmit,
  onCancel,
  securityHref,
}: StepUpDialogProps) {
  const formId = useId();
  const codeRef = useRef<HTMLInputElement>(null);
  const linkRef = useRef<HTMLAnchorElement>(null);
  const closeRef = useRef<HTMLButtonElement>(null);
  const ready = mfaState === "ready";

  // The code field was focused and is gone: the answer arrived with the dialog open.
  const previous = useRef(mfaState);
  useEffect(() => {
    const before = previous.current;
    previous.current = mfaState;
    if (!open || before !== "ready" || mfaState === "ready") return;
    (mfaState === "not-enrolled" ? linkRef : closeRef).current?.focus();
  }, [mfaState, open]);

  function close() {
    if (busy) return;
    onCancel();
  }

  let explanation: ReactNode = COPY.prompt;
  if (mfaState === "sso") explanation = COPY.sso;
  if (mfaState === "reauthenticate") explanation = COPY.reauthenticate;
  if (mfaState === "not-enrolled") {
    explanation = (
      <>
        {COPY.notEnrolled.before}
        <a
          ref={linkRef}
          href={securityHref}
          target="_blank"
          rel="noopener noreferrer"
          className="font-semibold text-accent underline underline-offset-2 hover:text-accent-strong"
        >
          {COPY.notEnrolled.link}
          <span className="sr-only">{COPY.notEnrolled.linkHint}</span>
        </a>
        {COPY.notEnrolled.after}
      </>
    );
  }

  return (
    <Dialog
      open={open}
      onClose={close}
      title={COPY.title}
      // Polite, so the sentence that replaces the prompt is read when the answer arrives.
      description={<div aria-live="polite">{explanation}</div>}
      size="sm"
      {...(ready ? { initialFocusRef: codeRef } : {})}
      footer={
        ready ? (
          <>
            <Button variant="ghost" onClick={close} disabled={busy}>
              {COPY.cancel}
            </Button>
            <Button type="submit" form={formId} loading={busy}>
              {busy ? COPY.verifying : COPY.verify}
            </Button>
          </>
        ) : (
          <Button ref={closeRef} variant="secondary" onClick={close}>
            {COPY.close}
          </Button>
        )
      }
    >
      {ready ? (
        <StepUpCodeForm
          formId={formId}
          inputRef={codeRef}
          busy={busy}
          error={error}
          onSubmit={onSubmit}
        />
      ) : null}
    </Dialog>
  );
}
