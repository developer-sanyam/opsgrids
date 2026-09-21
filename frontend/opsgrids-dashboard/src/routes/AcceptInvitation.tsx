import { useId, useState } from "react";
import type { FormEvent } from "react";
import { Link, useSearchParams } from "react-router-dom";

import { ApiError } from "@opsgrids/client";

import { acceptInvitation, requestPasswordLink } from "@/api";
import { AuthCard, Button, Field, InlineError, inputClass } from "@/ui";

/**
 * The public door an invited person walks through. The service returns one opaque 404 for every
 * failure (unknown, expired, already used) — the screen therefore says one thing for all of
 * them, never distinguishing "expired" from "never existed", which would confirm a guess.
 */
export function AcceptInvitationPage() {
  const [searchParams] = useSearchParams();
  const token = searchParams.get("token") ?? "";
  const nameId = useId();

  const [fullName, setFullName] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<{ email: string } | null>(null);

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    if (submitting) return;
    setError(null);
    if (!token) {
      setError("This invitation is not valid or has expired.");
      return;
    }
    setSubmitting(true);
    try {
      const member = await acceptInvitation(token, fullName.trim());
      // Accepting creates no credential — the invitee sets a password exactly as a fresh
      // sign-up does (Signup.tsx), by requesting the link for the address that was just
      // attached to a membership.
      await requestPasswordLink(member.email).catch(() => undefined);
      setDone({ email: member.email });
    } catch (err) {
      if (err instanceof ApiError && err.status === 404) {
        setError("This invitation is not valid or has expired.");
      } else if (err instanceof ApiError && err.status === 409) {
        setError(err.problem?.detail ?? "You already have an account in this organisation. Sign in instead.");
      } else {
        const detail = err instanceof Error ? err.message : "Something went wrong.";
        setError(`Couldn't accept the invitation. ${detail}`);
      }
    } finally {
      setSubmitting(false);
    }
  }

  if (!token) {
    return (
      <AuthCard title="This invitation is not valid or has expired">
        <p className="text-sm text-muted">
          Ask whoever invited you to send a new one. Already have an account?{" "}
          <Link to="/login" className="font-semibold text-ink underline-offset-2 hover:underline">
            Sign in
          </Link>
          .
        </p>
      </AuthCard>
    );
  }

  if (done) {
    return (
      <AuthCard title="Check your inbox">
        <div className="flex flex-col gap-4">
          <p className="text-sm text-ink-soft">
            You're in. We've sent a link to <strong>{done.email}</strong> that sets your password — it works once and expires.
          </p>
          <Link
            to="/login"
            className="inline-flex h-11 items-center self-start rounded-full border border-line bg-surface px-5 font-semibold text-ink transition-colors hover:bg-ground"
          >
            Go to sign in
          </Link>
        </div>
      </AuthCard>
    );
  }

  return (
    <AuthCard title="You've been invited to OpsGrids" intro="One more step, then you're in.">
      <form onSubmit={handleSubmit} className="flex flex-col gap-4" noValidate>
        {error ? <InlineError>{error}</InlineError> : null}

        <Field id={nameId} label="Your name">
          <input
            id={nameId}
            type="text"
            autoComplete="name"
            required
            value={fullName}
            onChange={(e) => setFullName(e.target.value)}
            placeholder="Asha Verma"
            className={inputClass}
          />
        </Field>

        <Button type="submit" disabled={submitting}>
          {submitting ? "Joining…" : "Accept invitation"}
        </Button>
      </form>
    </AuthCard>
  );
}
