import { useId, useState } from "react";
import type { FormEvent } from "react";
import { Link } from "react-router-dom";

import { requestPasswordLink } from "@/api";
import { AuthCard, Button, Field, InlineError, inputClass } from "@/ui";

/**
 * Ask for a set-password link. The service answers 202 whether or not the address exists —
 * a 404 here would tell whoever is asking which addresses are real — so the screen says the
 * same sentence either way, on purpose.
 */
export function ForgotPasswordPage() {
  const emailId = useId();
  const [email, setEmail] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [sent, setSent] = useState(false);

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    if (submitting) return;
    setError(null);
    setSubmitting(true);
    try {
      await requestPasswordLink(email.trim());
      setSent(true);
    } catch (err) {
      const detail = err instanceof Error ? err.message : "Something went wrong.";
      setError(`Couldn't send the link. ${detail}`);
    } finally {
      setSubmitting(false);
    }
  }

  if (sent) {
    return (
      <AuthCard title="If that address has an account, a link is on its way">
        <div className="flex flex-col gap-4">
          <p className="text-sm text-muted">
            It works once and expires. Nothing arrived after a few minutes? Check spam, then try again.
          </p>
          <Link to="/login" className="self-start text-sm font-semibold text-ink underline-offset-2 hover:underline">
            Back to sign in
          </Link>
        </div>
      </AuthCard>
    );
  }

  return (
    <AuthCard title="Set or reset your password" intro="Enter your work email and we will send a link.">
      <form onSubmit={handleSubmit} className="flex flex-col gap-4" noValidate>
        {error ? <InlineError>{error}</InlineError> : null}
        <Field id={emailId} label="Work email">
          <input
            id={emailId}
            type="email"
            autoComplete="email"
            required
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            placeholder="you@company.com"
            className={inputClass}
          />
        </Field>
        <Button type="submit" disabled={submitting}>
          {submitting ? "Sending…" : "Send the link"}
        </Button>
        <Link to="/login" className="self-start text-xs font-semibold text-muted underline-offset-2 hover:text-ink hover:underline">
          Back to sign in
        </Link>
      </form>
    </AuthCard>
  );
}
