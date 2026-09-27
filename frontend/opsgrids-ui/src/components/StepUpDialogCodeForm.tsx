import { useEffect, useRef, useState } from "react";
import type { ChangeEvent, FormEvent, RefObject } from "react";

import { TextField } from "./TextField";

/** What the last attempt at the code said. The dialog words it; the host only names it. */
export type StepUpDialogError = "wrong-code" | "locked" | "unavailable";

export const CODE_LENGTH = 6;

const COPY = {
  label: "6-digit code",
  incomplete: "Enter all 6 digits.",
  verifying: "Verifying…",
  errors: {
    "wrong-code":
      "That code didn't work. Wait for the next code in your authenticator app, then try again.",
    locked: "Too many attempts. Try again in a few minutes.",
    unavailable: "We couldn't check that code. Check your connection and try again.",
  },
} satisfies { label: string; incomplete: string; verifying: string; errors: Record<StepUpDialogError, string> };

/** Whatever was typed or pasted, reduced to the digits it holds: `123 456` and `123-456` are fine. */
function digitsOf(value: string): string {
  return value.replace(/\D/g, "").slice(0, CODE_LENGTH);
}

interface StepUpCodeFormProps {
  /** The id the dialog's Verify button names in its `form` attribute, so that button submits this. */
  formId: string;
  inputRef: RefObject<HTMLInputElement | null>;
  busy: boolean;
  error: StepUpDialogError | undefined;
  onSubmit: (code: string) => void;
}

/**
 * The code field. Its state lives here, inside the dialog, so closing the dialog empties it — a
 * code typed for one reveal is never sitting in the field for the next.
 *
 * **One field, not six boxes.** A six-box entry is six controls to a screen reader, breaks paste,
 * and fights the platform's own one-time-code autofill; a single field with `autocomplete=
 * "one-time-code"` and `inputmode="numeric"` gets the numeric keypad and the SMS/keychain
 * suggestion for free. Anything that is not a digit is dropped as it arrives, so a pasted
 * `123 456` lands as `123456`.
 *
 * **The sixth digit submits.** A code lasts thirty seconds; making the person reach for a button
 * spends some of it. Enter and the Verify button submit too, for a code that is still in the
 * field (a dropped connection keeps it, since the code may still be good).
 *
 * **A refused code is emptied and the field re-focused.** A code is single-use, so the digits that
 * were just refused are the one thing that can never work — leaving them there invites Enter,
 * which sends them again and counts a second failure toward the lockout. (`unavailable` says
 * nothing about the code, so that one keeps it.)
 */
export function StepUpCodeForm({ formId, inputRef, busy, error, onSubmit }: StepUpCodeFormProps) {
  const [code, setCode] = useState("");
  const [incomplete, setIncomplete] = useState(false);
  const [wasBusy, setWasBusy] = useState(busy);

  // The attempt just ended (adjusted during render, not in an effect, so the emptied field is
  // never painted with the refused digits in it).
  if (busy !== wasBusy) {
    setWasBusy(busy);
    if (wasBusy && (error === "wrong-code" || error === "locked")) setCode("");
  }

  // The field was read-only while the request was out, and a focused Verify button was disabled
  // under the person's finger: put them back where the next code goes.
  const attempted = useRef(false);
  useEffect(() => {
    if (busy) {
      attempted.current = true;
      return;
    }
    if (attempted.current) {
      attempted.current = false;
      inputRef.current?.focus();
    }
  }, [busy, inputRef]);

  function change(event: ChangeEvent<HTMLInputElement>) {
    const next = digitsOf(event.target.value);
    setIncomplete(false);
    if (next === code) return;
    setCode(next);
    if (next.length === CODE_LENGTH && !busy) onSubmit(next);
  }

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    // React bubbles a portalled dialog's submit through the component tree to whatever form the
    // screen opened it from; that form must not also submit.
    event.stopPropagation();
    if (busy) return;
    if (code.length < CODE_LENGTH) {
      setIncomplete(true);
      inputRef.current?.focus();
      return;
    }
    onSubmit(code);
  }

  const message = incomplete ? COPY.incomplete : error !== undefined ? COPY.errors[error] : undefined;

  return (
    <form id={formId} noValidate onSubmit={submit}>
      {/*
        The field's own error is described (aria-describedby) and read when focus arrives; this
        polite region is what says it when focus is *already* here — the answer to a code arrives
        after the last keystroke, with nothing for a screen reader to land on. One copy of the
        text serves both, so nothing is announced twice.
      */}
      <div aria-live="polite">
        <TextField
          ref={inputRef}
          label={COPY.label}
          required
          inputMode="numeric"
          autoComplete="one-time-code"
          autoCapitalize="none"
          spellCheck={false}
          enterKeyHint="go"
          value={code}
          onChange={change}
          readOnly={busy}
          className="text-center font-mono tnum tracking-wider"
          {...(message !== undefined ? { error: message } : {})}
        />
        {busy ? <p className="sr-only">{COPY.verifying}</p> : null}
      </div>
    </form>
  );
}
