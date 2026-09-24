import { useEffect, useRef, useState } from "react";

import { problemMessage, useMutation } from "@opsgrids/client";
import { Button, InlineAlert, QrCode, TextField } from "@opsgrids/ui";

import { useOperatorAuth } from "@/auth/context";
import type { EnrolmentSetup } from "@/auth/context";
import { isLocked, isRejected } from "@/auth/problems";
import { referenceOf } from "@/lib/errors";
import { SignInCard } from "./SignInCard";

interface EnrolScanStepProps {
  setup: EnrolmentSetup;
  /** Enrolment is confirmed. The recovery codes are shown once, so the page above shows them. */
  onConfirmed: (recoveryCodes: string[]) => void;
  onLocked: (message: string) => void;
  onBack: () => void;
}

/**
 * The second half of enrolment: scan the code (or type the key), then prove the authenticator
 * works with the code it shows. That proof completes the sign-in — there is no separate
 * verification step after it — so the answer carries the recovery codes.
 *
 * A wrong code says so beside the field in the server's words ("Check your device's clock" is the
 * usual cause), keeps the field and can be retried; it counts toward the same lockout as every
 * other second-factor attempt, and a lock returns the operator to the password step.
 */
export function EnrolScanStep({ setup, onConfirmed, onLocked, onBack }: EnrolScanStepProps) {
  const { confirmEnrolment } = useOperatorAuth();
  const [code, setCode] = useState("");
  const [codeError, setCodeError] = useState<string | undefined>(undefined);
  const codeRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    codeRef.current?.focus();
  }, []);

  const confirm = useMutation((value: string) => confirmEnrolment(value), {
    onSuccess: (recoveryCodes) => onConfirmed(recoveryCodes),
    onError: (error) => {
      if (isLocked(error)) {
        onLocked(problemMessage(error));
        return;
      }
      codeRef.current?.focus();
    },
  });

  function submit() {
    setCodeError(undefined);
    if (code.trim() === "") {
      setCodeError("Enter the 6-digit code your authenticator shows.");
      codeRef.current?.focus();
      return;
    }
    void confirm.mutate(code.trim());
  }

  const failure = confirm.error;
  const rejected = failure !== null && isRejected(failure);
  const otherFailure = failure !== null && !rejected && !isLocked(failure);
  const fieldMessage =
    codeError ?? (rejected ? problemMessage(failure) : undefined) ?? confirm.fieldErrors.code;

  return (
    <SignInCard
      title="Scan this with your authenticator"
      intro="Scan the code with an authenticator app, or enter the key by hand, then type the 6-digit code it shows."
    >
      <div className="flex flex-col gap-5">
        <div className="flex flex-col items-center gap-3">
          <div className="rounded-tile bg-surface p-2 shadow-card">
            <QrCode value={setup.otpauthUri} label="Authenticator setup code" size={192} />
          </div>
          <div className="flex w-full flex-col gap-1">
            <span className="text-xs font-semibold uppercase tracking-wide text-faint">
              Or enter this key
            </span>
            <code className="money break-all rounded-tile bg-ground px-3 py-2 text-sm text-ink select-all">
              {setup.secret}
            </code>
          </div>
        </div>

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
              title={`Couldn't confirm the code. ${problemMessage(failure)}`}
              reference={referenceOf(failure)}
            />
          ) : null}

          <TextField
            ref={codeRef}
            label="6-digit code"
            autoComplete="one-time-code"
            inputMode="numeric"
            maxLength={8}
            value={code}
            onChange={(event) => {
              setCode(event.target.value);
              setCodeError(undefined);
            }}
            placeholder="123456"
            error={fieldMessage}
          />

          <Button type="submit" loading={confirm.pending}>
            {confirm.pending ? "Confirming…" : "Confirm and sign in"}
          </Button>
          <button
            type="button"
            onClick={onBack}
            className="self-start text-xs font-semibold text-muted underline-offset-2 hover:text-ink hover:underline"
          >
            Back to sign in
          </button>
        </form>
      </div>
    </SignInCard>
  );
}
