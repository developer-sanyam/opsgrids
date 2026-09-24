import { useEffect, useId, useRef, useState } from "react";
import type { FormEvent } from "react";

import { useAuth } from "@/auth/context";
import { isLocked, isRejected, isResealPending } from "@/auth/problems";
import { Button } from "@/ui";
import { SignInShell } from "./SignInShell";

interface CodeStepProps {
  /** The account is locked and the sign-in with it: back to the password step, carrying why. */
  onLocked: (message: string) => void;
  /** The way back to the password step. */
  onBack: () => void;
}

/**
 * The second factor for an account that already has one: six digits from the authenticator app, or
 * a recovery code. Completing it makes the session, so this screen has no "next" — the redirect
 * lives on the page above and fires the moment the provider reports the operator signed in.
 *
 * Three failures, three different answers, told apart by the status the server sent and never by
 * its wording:
 *
 * - **401**, a wrong code: the server's sentence shown verbatim, the field keeps what was typed,
 *   and the pending sign-in survives — the next attempt goes through the same call.
 * - **403**, locked: the session behind this screen was revoked with the lock, so retrying is
 *   pointless — the operator is returned to the password step with the server's sentence.
 * - **503** `mfa-reseal-pending`: *the platform's* state, not the operator's code. A factor this
 *   deployment cannot yet read stopped a code that may well have been right, and the server
 *   deliberately did **not** count it toward the lockout. Said plainly, and it says what works: a
 *   recovery code, in the same field.
 */
export function CodeStep({ onLocked, onBack }: CodeStepProps) {
  const { verifyMfa } = useAuth();
  const codeId = useId();
  const [code, setCode] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [fieldError, setFieldError] = useState<string | null>(null);
  const [resealPending, setResealPending] = useState(false);
  const codeRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    codeRef.current?.focus();
  }, []);

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    setFieldError(null);
    setResealPending(false);
    if (code.trim() === "") {
      setFieldError("Enter the code from your authenticator app, or a recovery code.");
      codeRef.current?.focus();
      return;
    }
    setSubmitting(true);
    try {
      await verifyMfa(code.trim());
      // The redirect on success lives in LoginPage, driven by `status` — nothing to do here.
    } catch (error) {
      if (isLocked(error)) {
        onLocked(error.message);
        return;
      }
      if (isResealPending(error)) {
        setResealPending(true);
        setFieldError(error.message);
      } else if (isRejected(error)) {
        setFieldError(error.message);
      } else {
        const detail = error instanceof Error ? error.message : "Something went wrong.";
        setFieldError(`Couldn't check the code. ${detail}`);
      }
      codeRef.current?.focus();
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <SignInShell title="Enter your code" intro="From your authenticator app, or one of your recovery codes.">
      <form onSubmit={handleSubmit} className="flex flex-col gap-4" noValidate>
        {resealPending ? (
          <p role="alert" className="rounded-tile bg-pastdue-soft px-4 py-3 text-sm text-pastdue">
            <span className="block font-semibold">{fieldError}</span>
            <span className="mt-1 block">
              This is on our side, not your code, and it hasn&apos;t counted against you. Enter one
              of your recovery codes below to sign in.
            </span>
          </p>
        ) : fieldError ? (
          <p role="alert" className="rounded-tile bg-suspended-soft px-4 py-3 text-sm font-medium text-suspended">
            {fieldError}
          </p>
        ) : null}

        <div className="flex flex-col gap-1.5">
          <label htmlFor={codeId} className="text-sm font-semibold text-ink-soft">
            Code
          </label>
          <input
            id={codeId}
            ref={codeRef}
            autoComplete="one-time-code"
            autoCapitalize="none"
            spellCheck={false}
            maxLength={32}
            required
            value={code}
            onChange={(e) => setCode(e.target.value)}
            placeholder="123456"
            className="mono h-12 rounded-tile bg-ground px-4 text-sm text-ink placeholder:text-faint focus:outline-none focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2"
          />
        </div>

        <Button type="submit" size="md" disabled={submitting} className="w-full">
          {submitting ? "Verifying…" : "Verify"}
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
