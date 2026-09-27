import { useRef, useState } from "react";

import { ApiError, problemMessage, useMutation } from "@opsgrids/client";
import { Button, Card, InlineAlert, QrCode, TextField } from "@opsgrids/ui";

import { confirmMfaEnrolment } from "@/api/auth";
import type { MfaEnrolled, MfaEnrolStart } from "@/api/auth";

import { withoutSpaces } from "./mfaCode";

interface MfaConfirmFormProps {
  enrolment: MfaEnrolStart;
  onConfirmed: (result: MfaEnrolled) => void;
  /** The enrolment cannot be finished from here (begun elsewhere, or gone): begin a new one. */
  onStartAgain: () => void;
  onCancel: () => void;
}

const COPY = {
  title: "Scan this with your authenticator app",
  intro: "Scan the code, or enter the key by hand, then type the 6-digit code the app shows.",
  qrLabel: "Two-factor authentication setup code",
  codeLabel: "6-digit code",
  submit: "Confirm",
  submitting: "Confirming…",
  cancel: "Cancel",
  gone: {
    title: "This setup can't be finished from here.",
    body: "It was started in a different session, or it has expired. Start again to get a new code.",
    action: "Start again",
  },
};

/**
 * Step two: prove the app works. A wrong code is the field's own error (422, field `code`), and
 * the field is emptied — the code that just failed is not the one to try again. A 404 is not the
 * code's fault at all: the enrolment was begun by a different session, or is gone, so the form is
 * replaced by the way to begin again. Confirming a factor that is already confirmed is a 401, which
 * the shared client reads as a dead session (refresh, retry, sign out); this form cannot send it
 * twice, because it is gone the moment the first confirmation lands.
 */
export function MfaConfirmForm({ enrolment, onConfirmed, onStartAgain, onCancel }: MfaConfirmFormProps) {
  const [code, setCode] = useState("");
  const codeRef = useRef<HTMLInputElement>(null);

  const confirm = useMutation<void, MfaEnrolled>(
    () => confirmMfaEnrolment(enrolment.factor_id, withoutSpaces(code)),
    {
      onSuccess: onConfirmed,
      onError: (error) => {
        if (error instanceof ApiError && error.status === 422) {
          setCode("");
          codeRef.current?.focus();
        }
      },
    },
  );

  if (confirm.error instanceof ApiError && confirm.error.status === 404) {
    return (
      <Card className="flex flex-col gap-4 p-6">
        <h2 className="text-lg font-bold tracking-tight">{COPY.title}</h2>
        <InlineAlert
          tone="warning"
          title={COPY.gone.title}
          action={
            <Button type="button" size="sm" variant="secondary" onClick={onStartAgain}>
              {COPY.gone.action}
            </Button>
          }
        >
          {COPY.gone.body}
        </InlineAlert>
      </Card>
    );
  }

  const hasFieldErrors = Object.keys(confirm.fieldErrors).length > 0;

  return (
    <Card className="flex flex-col gap-4 p-6">
      <h2 className="text-lg font-bold tracking-tight">{COPY.title}</h2>
      <p className="text-sm text-muted">{COPY.intro}</p>
      <QrCode value={enrolment.otpauth_uri} label={COPY.qrLabel} />
      <p className="tnum break-all text-xs text-faint">{enrolment.secret}</p>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          void confirm.mutate();
        }}
        noValidate
        className="flex max-w-xs flex-col gap-4"
      >
        <TextField
          ref={codeRef}
          label={COPY.codeLabel}
          autoComplete="one-time-code"
          inputMode="numeric"
          spellCheck={false}
          value={code}
          onChange={(e) => {
            setCode(e.target.value);
            confirm.reset();
          }}
          error={confirm.fieldErrors.code}
        />
        {confirm.error && !hasFieldErrors ? <InlineAlert tone="danger" title={problemMessage(confirm.error)} /> : null}
        <div className="flex gap-2">
          <Button type="submit" loading={confirm.pending}>
            {confirm.pending ? COPY.submitting : COPY.submit}
          </Button>
          <Button type="button" variant="secondary" onClick={onCancel} disabled={confirm.pending}>
            {COPY.cancel}
          </Button>
        </div>
      </form>
    </Card>
  );
}
