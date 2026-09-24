import { useEffect, useId, useRef, useState } from "react";
import type { FormEvent } from "react";

import { useAuth } from "@/auth/context";
import type { EnrolmentSetup } from "@/auth/types";
import { isLocked, isRejected } from "@/auth/problems";
import { Button } from "@/ui";
import { SignInShell } from "./SignInShell";

interface EnrolCodeStepProps {
  onSetup: (setup: EnrolmentSetup) => void;
  onLocked: (message: string) => void;
  onBack: () => void;
}

/**
 * The first half of enrolment: the out-of-band code an owner issued. Staff MFA is mandatory, and a
 * password alone is not enough to reach enrolment — otherwise whoever guessed a password first
 * could enrol their own authenticator on someone else's account.
 *
 * **The code is single-use, and a typo spends it.** The server claims it before comparing, so a
 * wrong code cannot simply be retried — the operator needs a fresh one from an owner. A rejection
 * clears the field and repeats the server's sentence verbatim, since the value in it is now a spent
 * code and leaving it there invites a second doomed attempt.
 */
export function EnrolCodeStep({ onSetup, onLocked, onBack }: EnrolCodeStepProps) {
  const { beginEnrolment } = useAuth();
  const codeId = useId();
  const [code, setCode] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [fieldError, setFieldError] = useState<string | null>(null);
  const codeRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    codeRef.current?.focus();
  }, []);

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    setFieldError(null);
    if (code.trim() === "") {
      setFieldError("Enter the enrolment code an owner gave you.");
      codeRef.current?.focus();
      return;
    }
    setSubmitting(true);
    try {
      const setup = await beginEnrolment(code.trim());
      onSetup(setup);
    } catch (error) {
      if (isLocked(error)) {
        onLocked(error.message);
        return;
      }
      if (isRejected(error)) {
        setFieldError(error.message);
        setCode("");
      } else {
        const detail = error instanceof Error ? error.message : "Something went wrong.";
        setFieldError(`Couldn't start enrolment. ${detail}`);
      }
      codeRef.current?.focus();
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <SignInShell
      title="Set up your authenticator"
      intro="Your account needs a second factor before it can sign in. An owner gives you a one-time enrolment code to start."
    >
      <form onSubmit={handleSubmit} className="flex flex-col gap-4" noValidate>
        {fieldError ? (
          <p role="alert" className="rounded-tile bg-suspended-soft px-4 py-3 text-sm font-medium text-suspended">
            {fieldError}
          </p>
        ) : null}

        <div className="flex flex-col gap-1.5">
          <label htmlFor={codeId} className="text-sm font-semibold text-ink-soft">
            Enrolment code
          </label>
          <input
            id={codeId}
            ref={codeRef}
            autoComplete="off"
            autoCapitalize="none"
            spellCheck={false}
            maxLength={40}
            required
            value={code}
            onChange={(e) => setCode(e.target.value)}
            placeholder="a1b2c3d4-e5f6g7h8"
            className="mono h-12 rounded-tile bg-ground px-4 text-sm text-ink placeholder:text-faint focus:outline-none focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2"
          />
          <p className="text-xs text-faint">
            It works once and expires soon after it&apos;s issued. If it fails, ask an owner for a
            new one.
          </p>
        </div>

        <Button type="submit" size="md" disabled={submitting} className="w-full">
          {submitting ? "Checking…" : "Continue"}
        </Button>
        <button
          type="button"
          onClick={onBack}
          className="self-start text-xs font-semibold text-muted underline-offset-2 hover:text-ink hover:underline"
        >
          Back to sign in
        </button>
      </form>
    </SignInShell>
  );
}
