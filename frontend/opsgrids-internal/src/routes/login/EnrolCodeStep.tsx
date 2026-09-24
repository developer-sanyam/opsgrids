import { useEffect, useRef, useState } from "react";

import { problemMessage, useMutation } from "@opsgrids/client";
import { Button, InlineAlert, TextField } from "@opsgrids/ui";

import { useOperatorAuth } from "@/auth/context";
import type { EnrolmentSetup } from "@/auth/context";
import { isLocked, isRejected } from "@/auth/problems";
import { referenceOf } from "@/lib/errors";
import { SignInCard } from "./SignInCard";

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
 * **The code is single-use, and a typo spends it.** The server claims it before it compares, so a
 * wrong code cannot be retried; the operator needs a fresh one from an owner. The helper says so
 * up front, and a rejection repeats the server's sentence beside the field — the field is cleared
 * then, because the value in it is now a spent code and leaving it invites a second doomed try.
 */
export function EnrolCodeStep({ onSetup, onLocked, onBack }: EnrolCodeStepProps) {
  const { beginEnrolment } = useOperatorAuth();
  const [code, setCode] = useState("");
  const [codeError, setCodeError] = useState<string | undefined>(undefined);
  const codeRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    codeRef.current?.focus();
  }, []);

  const begin = useMutation((value: string) => beginEnrolment(value), {
    onSuccess: (setup) => onSetup(setup),
    onError: (error) => {
      if (isLocked(error)) {
        onLocked(problemMessage(error));
        return;
      }
      if (isRejected(error)) setCode("");
      codeRef.current?.focus();
    },
  });

  function submit() {
    setCodeError(undefined);
    if (code.trim() === "") {
      setCodeError("Enter the enrolment code an owner gave you.");
      codeRef.current?.focus();
      return;
    }
    void begin.mutate(code.trim());
  }

  const failure = begin.error;
  const rejected = failure !== null && isRejected(failure);
  const otherFailure = failure !== null && !rejected && !isLocked(failure);
  const fieldMessage =
    codeError ?? (rejected ? problemMessage(failure) : undefined) ?? begin.fieldErrors.enrollment_code;

  return (
    <SignInCard
      title="Set up your authenticator"
      intro="Your account needs a second factor before it can sign in. An owner gives you a one-time enrolment code to start."
    >
      <form
        onSubmit={(event) => {
          event.preventDefault();
          submit();
        }}
        noValidate
        className="flex flex-col gap-4"
      >
        {otherFailure ? (
          <InlineAlert
            tone="danger"
            title={`Couldn't start enrolment. ${problemMessage(failure)}`}
            reference={referenceOf(failure)}
          />
        ) : null}

        <TextField
          ref={codeRef}
          label="Enrolment code"
          autoComplete="off"
          autoCapitalize="none"
          spellCheck={false}
          maxLength={40}
          value={code}
          onChange={(event) => {
            setCode(event.target.value);
            setCodeError(undefined);
          }}
          helper="It works once and expires soon after it's issued. If it fails, ask an owner for a new one."
          error={fieldMessage}
        />

        <Button type="submit" loading={begin.pending}>
          {begin.pending ? "Checking…" : "Continue"}
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
