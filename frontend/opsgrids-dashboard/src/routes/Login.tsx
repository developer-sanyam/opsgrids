import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import { Link, Navigate, useLocation } from "react-router-dom";

import { ApiError, isAllowedRedirectTarget, problemMessage, useAuth } from "@opsgrids/client";
import type { LoginStep, OrganizationChoice } from "@opsgrids/client";
import { PasswordField, TextField } from "@opsgrids/ui";

import { AuthCard, Button, InlineError } from "@/ui";

/** Never leak which half was wrong — one message covers a bad email, bad password, or a 4xx. */
const CREDENTIAL_ERROR = "That email or password is incorrect.";

interface LocationState {
  from?: string;
}

/** A wanted path that is itself `/login` — B1 of the 2026-09-24 SSO review: a product app's own
 *  `/login` redirects here with `next=<its own URL>` whenever it finds no session, *including*
 *  the instant right after this very page sends the browser back to a `next` that, for whatever
 *  reason, still reads as signed out there. Honouring a `next` of `/login` would send the
 *  browser straight back here, which reports signed in and sends it right back again — the loop
 *  this whole file exists to close, reached from the dashboard's side of it this time. */
function isLoginPath(pathname: string): boolean {
  return /^\/login(?:\/|$)/i.test(pathname);
}

/**
 * A product SPA's `/login` redirects here with `?next=<its own URL>` (ADR-0003 addendum): the
 * dashboard is where sign-in actually happens, and once it completes the browser needs to go
 * back to whichever app sent it. `next` is attacker-controlled input — it rides in on a URL a
 * script on some other page can set — so it is trusted only after `isAllowedRedirectTarget`
 * confirms it names one of the platform's own app hosts *and* `isLoginPath` confirms it is not
 * that app's own `/login` (see above); anything else (another host entirely, a storefront
 * subdomain, a custom domain, `javascript:`, a protocol-relative value, a userinfo trick) is
 * silently ignored and sign-in falls back to the ordinary in-app destination.
 *
 * A full navigation (`window.location.assign`), not `<Navigate>`: the target is a different
 * origin, which React Router's client-side router cannot and must not try to reach.
 */
function externalDestination(search: string): string | null {
  const next = new URLSearchParams(search).get("next");
  if (!next || !isAllowedRedirectTarget(next)) return null;
  if (isLoginPath(new URL(next).pathname)) return null;
  return next;
}

/** A standalone text link or text button: quiet, and a 44px target. */
const TEXT_LINK =
  "inline-flex min-h-11 items-center self-start text-sm font-semibold text-muted underline-offset-2 hover:text-ink hover:underline";

/** What this screen renders. Distinct from `LoginStep`: it also carries the org list to show. */
type Step = { kind: "password" } | { kind: "mfa" } | { kind: "org_selection"; organizations: OrganizationChoice[] };

/**
 * The sign-in screen, entirely through `useAuth()`. `login()` and `verifyMfa()` both resolve to a
 * `LoginStep` — `complete`, `mfa_required`, or `org_selection` (a single organization auto-selects,
 * so that step only ever appears with a real choice to make) — and the provider is the one holding
 * the pre-auth token behind the last two; this component never sees it, whichever call minted it.
 *
 * A completed sign-in needs nothing further here: `status` flips to `authenticated` inside the
 * provider the moment any of `login` / `verifyMfa` / `selectOrg` finishes, so the redirect below
 * fires on the next render — no reload, nothing to hydrate by hand.
 */
export function LoginPage() {
  const { status, login, verifyMfa, selectOrg } = useAuth();
  const location = useLocation();

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [step, setStep] = useState<Step>({ kind: "password" });
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const destination = (location.state as LocationState | null)?.from ?? "/";
  const external = externalDestination(location.search);

  useEffect(() => {
    if (status === "authenticated" && external) window.location.assign(external);
  }, [status, external]);

  if (status === "authenticated") {
    // A hand-off back to another product app: hold here, with a real page navigation, while
    // the effect above sends the browser there. `<Navigate>` only knows this app's own routes
    // and cannot reach a different origin.
    if (external) {
      return (
        <AuthCard title="Signing you in…">
          <p role="status" aria-live="polite" className="text-sm text-muted">
            Taking you back to where you started…
          </p>
        </AuthCard>
      );
    }
    return <Navigate to={destination} replace />;
  }

  /** Land on the step a `login`/`verifyMfa` call answered with. `complete` needs nothing here. */
  function afterStep(next: LoginStep) {
    if (next.kind === "mfa_required") setStep({ kind: "mfa" });
    else if (next.kind === "org_selection") setStep({ kind: "org_selection", organizations: next.organizations });
    setSubmitting(false);
  }

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    if (submitting) return;
    setError(null);
    setSubmitting(true);
    try {
      afterStep(await login(email.trim(), password));
    } catch (err) {
      if (err instanceof ApiError && err.status >= 400 && err.status < 500) {
        setError(CREDENTIAL_ERROR);
      } else {
        setError(`Couldn't sign in. ${problemMessage(err)}`);
      }
      setSubmitting(false);
    }
  }

  async function handleVerifyMfa(event: FormEvent) {
    event.preventDefault();
    if (submitting) return;
    setError(null);
    setSubmitting(true);
    try {
      afterStep(await verifyMfa(code.trim()));
    } catch (err) {
      // The server's own sentence ("That code is not valid") — the pending sign-in survives a
      // wrong code, so the field stays and the next attempt goes through the same call.
      setError(problemMessage(err));
      setSubmitting(false);
    }
  }

  async function handlePickOrg(organizationId: string) {
    if (submitting) return;
    setError(null);
    setSubmitting(true);
    try {
      await selectOrg(organizationId);
      // `status` flips to authenticated inside `selectOrg`; the redirect above takes it from here.
    } catch (err) {
      setError(`Couldn't finish signing in. ${problemMessage(err)}`);
      setSubmitting(false);
    }
  }

  function backToPassword() {
    setStep({ kind: "password" });
    setCode("");
    setError(null);
  }

  if (step.kind === "org_selection") {
    return (
      <AuthCard title="Choose a workspace">
        <div className="flex flex-col gap-4">
          {error ? <InlineError>{error}</InlineError> : null}
          <ul className="flex list-none flex-col gap-2 p-0">
            {step.organizations.map((org) => (
              <li key={org.organization_id}>
                <button
                  type="button"
                  disabled={submitting}
                  onClick={() => void handlePickOrg(org.organization_id)}
                  className="min-h-11 w-full rounded-tile border border-field-edge bg-surface px-4 py-3 text-left text-sm font-semibold text-ink transition-colors hover:bg-sunken disabled:cursor-not-allowed disabled:text-disabled"
                >
                  {org.name}
                </button>
              </li>
            ))}
          </ul>
        </div>
      </AuthCard>
    );
  }

  if (step.kind === "mfa") {
    return (
      <AuthCard title="Enter your code" intro="From your authenticator app, or one of your recovery codes.">
        <form onSubmit={handleVerifyMfa} className="flex flex-col gap-4" noValidate>
          {error ? <InlineError>{error}</InlineError> : null}
          {/* Recovery codes mix letters and numbers, so no numeric keypad and no auto-capitalising. */}
          <TextField
            label="Code"
            type="text"
            autoComplete="one-time-code"
            autoCapitalize="none"
            spellCheck={false}
            required
            value={code}
            onChange={(e) => setCode(e.target.value)}
            placeholder="123456"
          />
          <Button type="submit" loading={submitting}>
            {submitting ? "Verifying…" : "Verify"}
          </Button>
          <button type="button" onClick={backToPassword} className={TEXT_LINK}>
            Back to sign in
          </button>
        </form>
      </AuthCard>
    );
  }

  return (
    <AuthCard
      title="Sign in to OpsGrids"
      intro={
        <>
          New here?{" "}
          <Link to="/signup" className="font-semibold text-ink underline-offset-2 hover:underline">
            Create your organisation
          </Link>
          .
        </>
      }
    >
      <form onSubmit={handleSubmit} className="flex flex-col gap-4" noValidate>
        {error ? <InlineError>{error}</InlineError> : null}

        <TextField
          label="Work email"
          type="email"
          autoComplete="email"
          required
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          placeholder="you@company.com"
        />

        <PasswordField value={password} onChange={setPassword} placeholder="••••••••" />

        <Button type="submit" loading={submitting}>
          {submitting ? "Signing in…" : "Sign in"}
        </Button>

        <Link to="/forgot-password" className={TEXT_LINK}>
          Forgot your password?
        </Link>
      </form>
    </AuthCard>
  );
}
