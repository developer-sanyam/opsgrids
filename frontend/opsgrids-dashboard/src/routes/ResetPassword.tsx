import { useId, useState } from "react";
import type { FormEvent } from "react";
import { Link, useSearchParams } from "react-router-dom";

import { ApiError } from "@opsgrids/client";

import { setPasswordFromLink } from "@/api";
import { AuthCard, Button, Field, InlineError, inputClass } from "@/ui";

/** Mirrors auth's `Password` constraint closely enough to fail fast; the service is the judge. */
const MIN_LENGTH = 12;

/**
 * The landing page of the emailed link: `/reset-password?token=…`. Used by a fresh sign-up
 * setting a first password and by anyone resetting one — the service does not distinguish, and
 * neither does the wording.
 */
export function ResetPasswordPage() {
  const [params] = useSearchParams();
  const token = params.get("token") ?? "";

  const passwordId = useId();
  const confirmId = useId();
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    if (submitting) return;
    setError(null);
    if (password.length < MIN_LENGTH) {
      setError(`Use at least ${MIN_LENGTH} characters.`);
      return;
    }
    if (password !== confirm) {
      setError("The two passwords do not match.");
      return;
    }
    setSubmitting(true);
    try {
      await setPasswordFromLink(token, password);
      setDone(true);
    } catch (err) {
      if (err instanceof ApiError && err.status === 404) {
        setError("That link is not valid any more. Ask for a new one below.");
      } else if (err instanceof ApiError && err.status === 422) {
        setError(err.problem?.detail ?? "That password is not strong enough.");
      } else {
        const detail = err instanceof Error ? err.message : "Something went wrong.";
        setError(`Couldn't set the password. ${detail}`);
      }
    } finally {
      setSubmitting(false);
    }
  }

  if (!token) {
    return (
      <AuthCard title="This link is missing its token">
        <p className="text-sm text-muted">
          Open the link from the email exactly as it was sent, or{" "}
          <Link to="/forgot-password" className="font-semibold text-ink underline-offset-2 hover:underline">
            ask for a new one
          </Link>
          .
        </p>
      </AuthCard>
    );
  }

  if (done) {
    return (
      <AuthCard title="Your password is set">
        <div className="flex flex-col gap-4">
          <p className="text-sm text-muted">Every other session on this account has been signed out.</p>
          <Link
            to="/login"
            className="inline-flex h-11 items-center self-start rounded-full bg-accent px-5 font-semibold text-white transition-colors hover:bg-accent-strong"
          >
            Sign in
          </Link>
        </div>
      </AuthCard>
    );
  }

  return (
    <AuthCard title="Choose a password">
      <form onSubmit={handleSubmit} className="flex flex-col gap-4" noValidate>
        {error ? (
          <InlineError>
            {error}{" "}
            {error.includes("new one") ? (
              <Link to="/forgot-password" className="underline">
                Request a link
              </Link>
            ) : null}
          </InlineError>
        ) : null}
        <Field id={passwordId} label="New password" hint={`At least ${MIN_LENGTH} characters.`}>
          <input
            id={passwordId}
            type="password"
            autoComplete="new-password"
            required
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            className={inputClass}
          />
        </Field>
        <Field id={confirmId} label="Once more">
          <input
            id={confirmId}
            type="password"
            autoComplete="new-password"
            required
            value={confirm}
            onChange={(e) => setConfirm(e.target.value)}
            className={inputClass}
          />
        </Field>
        <Button type="submit" disabled={submitting}>
          {submitting ? "Saving…" : "Set password"}
        </Button>
      </form>
    </AuthCard>
  );
}
