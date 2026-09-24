import { useEffect, useRef, useState } from "react";

import { problemMessage, useMutation } from "@opsgrids/client";
import { Button, InlineAlert, TextField } from "@opsgrids/ui";

import { useOperatorAuth } from "@/auth/context";
import { isLocked, isRejected, isResealPending } from "@/auth/problems";
import { referenceOf } from "@/lib/errors";
import { SignInCard } from "./SignInCard";

interface CodeStepProps {
  /** The account is locked and the sign-in with it: back to the password step, carrying why. */
  onLocked: (message: string) => void;
  /** The way back to the password step. */
  onBack: () => void;
}

/**
 * The second factor for an account that has one: six digits from the authenticator app, or a
 * recovery code. Completing it makes the session, so this screen has no "next" — the redirect
 * lives in the page above and fires the moment the provider reports the operator signed in.
 *
 * Four failures, four different answers, told apart by the status the server sent and never by
 * its wording:
 *
 * - **401**, a wrong code: the server's sentence sits beside the field, the field keeps what was
 *   typed, and the pending sign-in survives, so the next attempt goes through the same call.
 * - **403**, locked: the session behind this screen was revoked with the lock, so retrying is
 *   pointless — the operator is returned to the password step with the server's sentence.
 * - **503** `mfa-reseal-pending`: *the platform's* state, not the operator's code. A factor this
 *   deployment cannot yet read stopped a code that may well have been right, and the server
 *   deliberately did **not** count it toward the lockout. It is said plainly, and it says what
 *   works: a recovery code, in the same field.
 * - anything else — a dropped connection, a 500 — is a failure to check, not a wrong code.
 */
export function CodeStep({ onLocked, onBack }: CodeStepProps) {
  const { verifyMfa } = useOperatorAuth();
  const [code, setCode] = useState("");
  const [codeError, setCodeError] = useState<string | undefined>(undefined);
  const codeRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    codeRef.current?.focus();
  }, []);

  const verify = useMutation((value: string) => verifyMfa(value), {
    onError: (error) => {
      if (isLocked(error)) {
        onLocked(problemMessage(error));
        return;
      }
      // Whatever the failure, the field is where the operator's next move is.
      codeRef.current?.focus();
    },
  });

  function submit() {
    setCodeError(undefined);
    if (code.trim() === "") {
      setCodeError("Enter the code from your authenticator app, or a recovery code.");
      codeRef.current?.focus();
      return;
    }
    void verify.mutate(code.trim());
  }

  const failure = verify.error;
  const resealPending = failure !== null && isResealPending(failure);
  const rejected = failure !== null && isRejected(failure);
  const otherFailure = failure !== null && !resealPending && !rejected && !isLocked(failure);
  // The sentence beside the field: our own reminder for an empty submit, else the server's for a
  // wrong code, else the field-level detail a 422 names.
  const fieldMessage =
    codeError ?? (rejected ? problemMessage(failure) : undefined) ?? verify.fieldErrors.code;

  return (
    <SignInCard title="Enter your code" intro="From your authenticator app, or one of your recovery codes.">
      <form
        onSubmit={(event) => {
          event.preventDefault();
          submit();
        }}
        noValidate
        className="flex flex-col gap-4"
      >
        {resealPending ? (
          <InlineAlert
            tone="warning"
            title={problemMessage(failure)}
            reference={referenceOf(failure)}
          >
            This is on our side, not your code, and it hasn't counted against you. Enter one of your
            recovery codes below to sign in.
          </InlineAlert>
        ) : null}
        {otherFailure ? (
          <InlineAlert
            tone="danger"
            title={`Couldn't check the code. ${problemMessage(failure)}`}
            reference={referenceOf(failure)}
          />
        ) : null}

        <TextField
          ref={codeRef}
          label="Code"
          autoComplete="one-time-code"
          autoCapitalize="none"
          spellCheck={false}
          maxLength={32}
          value={code}
          onChange={(event) => {
            setCode(event.target.value);
            setCodeError(undefined);
          }}
          placeholder="123456"
          error={fieldMessage}
        />

        <Button type="submit" loading={verify.pending}>
          {verify.pending ? "Verifying…" : "Verify"}
        </Button>
        <button
          type="button"
          onClick={onBack}
          className="self-start text-xs font-semibold text-muted underline-offset-2 hover:text-ink hover:underline"
        >
          Back to sign in
        </button>
      </form>
    </SignInCard>
  );
}
