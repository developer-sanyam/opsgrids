import { useEffect, useRef, useState } from "react";

import { ApiError, problemMessage, problemSlug, useMutation } from "@opsgrids/client";
import { Button, Card, InlineAlert, TextField } from "@opsgrids/ui";

import { startMfaEnrolment } from "@/api/auth";
import type { MfaEnrolInput, MfaEnrolStart } from "@/api/auth";

import { withoutSpaces } from "./mfaCode";

interface MfaStartFormProps {
  /**
   * The account already has an authenticator, so this adds another and a code from the existing
   * one is asked for up front. When it is `false` the code field appears only if the server says
   * it is needed (`mfa-current-code-required`) — the page's idea of "has one" can be stale.
   */
  adding: boolean;
  onStarted: (enrolment: MfaEnrolStart) => void;
  /** The server refused because this session came from single sign-on: nothing here can work. */
  onSingleSignOn: () => void;
  onCancel: () => void;
}

/** What can be said before anything is sent: a field left empty. Keyed like the server's `errors[]`. */
interface LocalProblems {
  password?: string | undefined;
  current_code?: string | undefined;
}

const COPY = {
  first: {
    title: "Set up two-factor authentication",
    intro: "Enter your password to continue. We'll then show a code to scan with your authenticator app.",
  },
  adding: {
    title: "Add another authenticator",
    intro:
      "Adding an authenticator changes how your account is reached, so we need to check it's you: your password, and a code from an authenticator you already use.",
  },
  passwordLabel: "Password",
  codeLabel: "Current authenticator code",
  codeHelper: "A 6-digit code from an authenticator you already use, or one of your recovery codes.",
  needPassword: "Enter your password.",
  needCode: "Enter a code from your current authenticator.",
  submit: "Continue",
  submitting: "Checking…",
  cancel: "Cancel",
};

/**
 * Step one of adding an authenticator: prove who is asking. The password is always needed; the
 * code from an authenticator already on the account is needed as well when there is one.
 *
 * Every refusal lands where it belongs. A wrong password goes under the password field, and a
 * missing, wrong or already-used code under the code field — each cleared, because neither can
 * be resent. A lockout is not a field's fault and is said above the button. A wrong value is a
 * 422 and counts toward a lockout, so nothing here retries one.
 */
export function MfaStartForm({ adding, onStarted, onSingleSignOn, onCancel }: MfaStartFormProps) {
  const [password, setPassword] = useState("");
  const [currentCode, setCurrentCode] = useState("");
  const [serverWantsCode, setServerWantsCode] = useState(false);
  const [missing, setMissing] = useState<LocalProblems>({});
  const passwordRef = useRef<HTMLInputElement>(null);
  const codeRef = useRef<HTMLInputElement>(null);
  const askForCode = adding || serverWantsCode;

  const start = useMutation<MfaEnrolInput, MfaEnrolStart>((input) => startMfaEnrolment(input), {
    onSuccess: onStarted,
    onError: (error) => {
      if (!(error instanceof ApiError)) return;
      switch (problemSlug(error.problem)) {
        case "mfa-unavailable-for-sso":
          onSingleSignOn();
          break;
        case "mfa-current-code-required":
          setServerWantsCode(true);
          break;
        case "password-incorrect":
          setPassword("");
          passwordRef.current?.focus();
          break;
        case "mfa-code-invalid":
        case "mfa-code-replayed":
          setCurrentCode("");
          codeRef.current?.focus();
          break;
        default:
          break;
      }
    },
  });

  // The field only exists once the server has asked for it; the person should land in it.
  useEffect(() => {
    if (serverWantsCode) codeRef.current?.focus();
  }, [serverWantsCode]);

  function submit() {
    const problems: LocalProblems = {};
    if (password === "") problems.password = COPY.needPassword;
    const code = withoutSpaces(currentCode);
    if (askForCode && code === "") problems.current_code = COPY.needCode;
    setMissing(problems);
    if (problems.password !== undefined) {
      passwordRef.current?.focus();
      return;
    }
    if (problems.current_code !== undefined) {
      codeRef.current?.focus();
      return;
    }
    void start.mutate({ password, currentCode: askForCode ? code : undefined });
  }

  const copy = adding ? COPY.adding : COPY.first;
  const hasFieldErrors = Object.keys(start.fieldErrors).length > 0;

  return (
    <Card className="flex flex-col gap-4 p-6">
      <h2 className="text-lg font-bold tracking-tight">{copy.title}</h2>
      <p className="text-sm text-muted">{copy.intro}</p>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          submit();
        }}
        noValidate
        className="flex max-w-sm flex-col gap-4"
      >
        <TextField
          ref={passwordRef}
          label={COPY.passwordLabel}
          type="password"
          autoComplete="current-password"
          required
          value={password}
          onChange={(e) => {
            setPassword(e.target.value);
            setMissing((previous) => ({ ...previous, password: undefined }));
            start.reset();
          }}
          error={missing.password ?? start.fieldErrors.password}
        />
        {askForCode ? (
          <TextField
            ref={codeRef}
            label={COPY.codeLabel}
            autoComplete="one-time-code"
            inputMode="numeric"
            spellCheck={false}
            required
            helper={COPY.codeHelper}
            value={currentCode}
            onChange={(e) => {
              setCurrentCode(e.target.value);
              setMissing((previous) => ({ ...previous, current_code: undefined }));
              start.reset();
            }}
            error={missing.current_code ?? start.fieldErrors.current_code}
          />
        ) : null}
        {start.error && !hasFieldErrors ? <InlineAlert tone="danger" title={problemMessage(start.error)} /> : null}
        <div className="flex gap-2">
          <Button type="submit" loading={start.pending}>
            {start.pending ? COPY.submitting : COPY.submit}
          </Button>
          <Button type="button" variant="secondary" onClick={onCancel} disabled={start.pending}>
            {COPY.cancel}
          </Button>
        </div>
      </form>
    </Card>
  );
}
