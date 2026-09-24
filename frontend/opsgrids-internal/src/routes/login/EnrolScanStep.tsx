import { useEffect, useId, useRef, useState } from "react";
import type { FormEvent } from "react";

import { QrCode } from "@opsgrids/ui";

import { useAuth } from "@/auth/context";
import type { EnrolmentSetup } from "@/auth/types";
import { isLocked } from "@/auth/problems";
import { Button } from "@/ui";
import { SignInShell } from "./SignInShell";

interface EnrolScanStepProps {
  setup: EnrolmentSetup;
  /** Enrolment is confirmed. The recovery codes are shown once, so the page above shows them. */
  onConfirmed: (recoveryCodes: string[]) => void;
  onLocked: (message: string) => void;
  onBack: () => void;
}

/**
 * The second half of enrolment: scan the code — or type the key by hand — then prove the
 * authenticator works with the code it shows. That proof completes the sign-in; there is no
 * separate verification step after it, so the answer carries the recovery codes.
 *
 * `QrCode` is `@opsgrids/ui`'s own hand-rolled encoder (`src/lib/qr.ts`, no external QR service and
 * no network round trip to render it) — this screen is the one place in the console that reaches
 * into the shared design system, for exactly this component and nothing else it exports.
 */
export function EnrolScanStep({ setup, onConfirmed, onLocked, onBack }: EnrolScanStepProps) {
  const { confirmEnrolment } = useAuth();
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
      setFieldError("Enter the 6-digit code your authenticator shows.");
      codeRef.current?.focus();
      return;
    }
    setSubmitting(true);
    try {
      const recoveryCodes = await confirmEnrolment(code.trim());
      onConfirmed(recoveryCodes);
    } catch (error) {
      if (isLocked(error)) {
        onLocked(error.message);
        return;
      }
      const detail = error instanceof Error ? error.message : "Something went wrong.";
      setFieldError(detail);
      codeRef.current?.focus();
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <SignInShell
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
            <code className="mono block break-all rounded-tile bg-ground px-3 py-2 text-sm text-ink select-all">
              {setup.secret}
            </code>
          </div>
        </div>

        <form onSubmit={handleSubmit} className="flex flex-col gap-4" noValidate>
          {fieldError ? (
            <p role="alert" className="rounded-tile bg-danger-soft px-4 py-3 text-sm font-medium text-danger">
              {fieldError}
            </p>
          ) : null}

          <div className="flex flex-col gap-1.5">
            <label htmlFor={codeId} className="text-sm font-semibold text-ink-soft">
              6-digit code
            </label>
            <input
              id={codeId}
              ref={codeRef}
              autoComplete="one-time-code"
              inputMode="numeric"
              maxLength={8}
              required
              value={code}
              onChange={(e) => setCode(e.target.value)}
              placeholder="123456"
              className="mono h-12 rounded-tile bg-ground px-4 text-sm text-ink placeholder:text-faint focus:outline-none focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2"
            />
          </div>

          <Button type="submit" size="md" disabled={submitting} className="w-full">
            {submitting ? "Confirming…" : "Confirm and sign in"}
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
    </SignInShell>
  );
}
