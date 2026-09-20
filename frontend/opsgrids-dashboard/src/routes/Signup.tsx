import { useId, useState } from "react";
import type { FormEvent } from "react";
import { Link } from "react-router-dom";

import { ApiError } from "@opsgrids/client";

import { requestPasswordLink, signup } from "@/api";
import { SLUG_RULE, suggestSlug } from "@/lib/slug";
import { AuthCard, Button, Field, InlineError, inputClass } from "@/ui";

/**
 * Sign-up (ADR-0013 §3). Four fields and **no password**: the organisation and its owner are
 * created, then the set-password link is requested for that address. The screen ends on "check
 * your inbox" because that is where the next step is — and because an address that cannot
 * receive the link never gets a password, which is the whole of email verification here.
 */
export function SignupPage() {
  const orgId = useId();
  const slugId = useId();
  const nameId = useId();
  const emailId = useId();

  const [organizationName, setOrganizationName] = useState("");
  const [slug, setSlug] = useState("");
  const [slugTouched, setSlugTouched] = useState(false);
  const [fullName, setFullName] = useState("");
  const [email, setEmail] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<{ email: string; slug: string } | null>(null);

  function handleNameChange(value: string) {
    setOrganizationName(value);
    if (!slugTouched) setSlug(suggestSlug(value));
  }

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    if (submitting) return;
    setError(null);
    const trimmedEmail = email.trim();
    if (!SLUG_RULE.test(slug)) {
      setError("The address can only use lowercase letters, digits and hyphens, and must start with a letter or digit.");
      return;
    }
    setSubmitting(true);
    try {
      const created = await signup({
        organization_name: organizationName.trim(),
        slug,
        full_name: fullName.trim(),
        email: trimmedEmail,
      });
      // The password comes by link. Requesting it is a separate, public, always-202 call, so
      // a failure here would be a mail problem rather than a sign-up problem — and the person
      // can ask again from the sign-in screen.
      await requestPasswordLink(created.email).catch(() => undefined);
      setDone({ email: created.email, slug: created.slug });
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        setError(err.problem?.detail ?? "That address or email is already taken.");
      } else if (err instanceof ApiError && err.status === 422) {
        setError("Please check the fields — something is not in the shape we expected.");
      } else {
        const detail = err instanceof Error ? err.message : "Something went wrong.";
        setError(`Couldn't create the organisation. ${detail}`);
      }
    } finally {
      setSubmitting(false);
    }
  }

  if (done) {
    return (
      <AuthCard title="Check your inbox">
        <div className="flex flex-col gap-4">
          <p className="text-sm text-ink-soft">
            Your organisation <strong>{done.slug}</strong> exists. We have sent a link to{" "}
            <strong>{done.email}</strong> that sets your password — it works once and expires.
          </p>
          <p className="text-sm text-muted">
            Nothing arrived? Check spam, then{" "}
            <Link to="/forgot-password" className="font-semibold text-ink underline-offset-2 hover:underline">
              ask for another link
            </Link>
            .
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
    <AuthCard
      title="Create your organisation"
      intro={
        <>
          Free for 14 days on any product, no card needed. Already have an account?{" "}
          <Link to="/login" className="font-semibold text-ink underline-offset-2 hover:underline">
            Sign in
          </Link>
          .
        </>
      }
    >
      <form onSubmit={handleSubmit} className="flex flex-col gap-4" noValidate>
        {error ? <InlineError>{error}</InlineError> : null}

        <Field id={orgId} label="Organisation name">
          <input
            id={orgId}
            type="text"
            autoComplete="organization"
            required
            value={organizationName}
            onChange={(e) => handleNameChange(e.target.value)}
            placeholder="Greenleaf Organics"
            className={inputClass}
          />
        </Field>

        <Field id={slugId} label="Address" hint="Your organisation's short name inside OpsGrids. Lowercase letters, digits and hyphens.">
          <input
            id={slugId}
            type="text"
            autoComplete="off"
            required
            value={slug}
            onChange={(e) => {
              setSlugTouched(true);
              setSlug(e.target.value.toLowerCase());
            }}
            placeholder="greenleaf"
            className={inputClass}
          />
        </Field>

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

        <Field id={emailId} label="Work email" hint="The link that sets your password goes here.">
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
          {submitting ? "Creating…" : "Create organisation"}
        </Button>
      </form>
    </AuthCard>
  );
}
