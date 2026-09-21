import { useId, useState } from "react";
import type { FormEvent } from "react";
import { Link, Navigate, useLocation } from "react-router-dom";

import { ApiError, authApi, useAuth } from "@opsgrids/client";
import type { LoginResult, OrganizationChoice } from "@opsgrids/client";
import { EyeIcon, EyeOffIcon } from "@opsgrids/ui";

import { completeSessionAndReload, mfaVerify, selectOrgWithToken } from "@/api/auth";
import { AuthCard, Button, Field, InlineError, inputClass } from "@/ui";

/** Never leak which half was wrong — one message covers a bad email, bad password, or a 4xx. */
const CREDENTIAL_ERROR = "That email or password is incorrect.";

interface LocationState {
  from?: string;
}

/**
 * The sign-in screen. Signed in **directly through `authApi`**, not `useAuth().login()`: the
 * MFA branch needs the raw `pre_auth_token` the result carries, and the shared context throws
 * it away once it decides the step — see `src/api/auth.ts` for why, and for the exact change
 * that belongs in `@opsgrids/client` instead of this workaround. Every completed sign-in ends
 * in a full reload so `AuthProvider`'s own boot effect picks the stored token up through the
 * one path that already does that correctly.
 */
export function LoginPage() {
  const { status } = useAuth();
  const location = useLocation();

  const emailId = useId();
  const passwordId = useId();
  const codeId = useId();

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [orgs, setOrgs] = useState<{ token: string; organizations: OrganizationChoice[] } | null>(null);
  const [mfaToken, setMfaToken] = useState<string | null>(null);
  const [code, setCode] = useState("");

  const destination = (location.state as LocationState | null)?.from ?? "/";

  if (status === "authenticated") return <Navigate to={destination} replace />;

  function handleResult(result: LoginResult) {
    if (result.status === "complete") {
      completeSessionAndReload(result.access_token, destination);
      return;
    }
    if (result.status === "org_selection_required") {
      if (result.organizations.length === 1) {
        const only = result.organizations[0];
        if (only) {
          void handlePickOrg(result.pre_auth_token, only.organization_id);
          return;
        }
      }
      setOrgs({ token: result.pre_auth_token, organizations: result.organizations });
      setMfaToken(null);
      setSubmitting(false);
      return;
    }
    // mfa_required
    setMfaToken(result.pre_auth_token);
    setOrgs(null);
    setSubmitting(false);
  }

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    if (submitting) return;
    setError(null);
    setSubmitting(true);
    try {
      const result = await authApi.login(email.trim(), password);
      handleResult(result);
    } catch (err) {
      if (err instanceof ApiError && err.status >= 400 && err.status < 500) {
        setError(CREDENTIAL_ERROR);
      } else {
        const detail = err instanceof Error ? err.message : "Something went wrong.";
        setError(`Couldn't sign in. ${detail}`);
      }
      setSubmitting(false);
    }
  }

  async function handleVerifyMfa(event: FormEvent) {
    event.preventDefault();
    if (submitting || !mfaToken) return;
    setError(null);
    setSubmitting(true);
    try {
      const result = await mfaVerify(mfaToken, code.trim());
      handleResult(result);
    } catch (err) {
      if (err instanceof ApiError && err.status >= 400 && err.status < 500) {
        setError("That code is incorrect or has expired.");
      } else {
        const detail = err instanceof Error ? err.message : "Something went wrong.";
        setError(`Couldn't verify that code. ${detail}`);
      }
      setSubmitting(false);
    }
  }

  async function handlePickOrg(token: string, organizationId: string) {
    setError(null);
    setSubmitting(true);
    try {
      const result = await selectOrgWithToken(token, organizationId);
      handleResult(result);
    } catch (err) {
      const detail = err instanceof Error ? err.message : "Something went wrong.";
      setError(`Couldn't finish signing in. ${detail}`);
      setSubmitting(false);
    }
  }

  if (orgs) {
    return (
      <AuthCard title="Choose a workspace">
        <div className="flex flex-col gap-4">
          {error ? <InlineError>{error}</InlineError> : null}
          <ul className="flex list-none flex-col gap-2 p-0">
            {orgs.organizations.map((org) => (
              <li key={org.organization_id}>
                <button
                  type="button"
                  disabled={submitting}
                  onClick={() => void handlePickOrg(orgs.token, org.organization_id)}
                  className="w-full rounded-tile border border-line bg-surface px-4 py-3 text-left text-sm font-semibold text-ink transition-colors hover:bg-ground disabled:cursor-not-allowed disabled:text-faint"
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

  if (mfaToken) {
    return (
      <AuthCard title="Enter your code" intro="From your authenticator app, or one of your recovery codes.">
        <form onSubmit={handleVerifyMfa} className="flex flex-col gap-4" noValidate>
          {error ? <InlineError>{error}</InlineError> : null}
          <Field id={codeId} label="Code">
            <input
              id={codeId}
              type="text"
              inputMode="numeric"
              autoComplete="one-time-code"
              required
              value={code}
              onChange={(e) => setCode(e.target.value)}
              placeholder="123456"
              className={inputClass}
            />
          </Field>
          <Button type="submit" disabled={submitting}>
            {submitting ? "Verifying…" : "Verify"}
          </Button>
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

        <Field id={passwordId} label="Password">
          <div className="flex items-center rounded-tile bg-ground pr-1.5 focus-within:outline focus-within:outline-2 focus-within:outline-offset-2 focus-within:outline-ink">
            <input
              id={passwordId}
              type={showPassword ? "text" : "password"}
              autoComplete="current-password"
              required
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="••••••••"
              className="h-12 min-w-0 flex-1 bg-transparent px-4 text-sm text-ink placeholder:text-faint focus:outline-none"
            />
            <button
              type="button"
              onClick={() => setShowPassword((v) => !v)}
              aria-label={showPassword ? "Hide password" : "Show password"}
              aria-pressed={showPassword}
              className="grid size-9 place-items-center rounded-full text-muted transition-colors hover:bg-line hover:text-ink"
            >
              {showPassword ? <EyeOffIcon className="size-5" /> : <EyeIcon className="size-5" />}
            </button>
          </div>
        </Field>

        <Button type="submit" disabled={submitting}>
          {submitting ? "Signing in…" : "Sign in"}
        </Button>

        <Link
          to="/forgot-password"
          className="self-start text-xs font-semibold text-muted underline-offset-2 hover:text-ink hover:underline"
        >
          Forgot your password?
        </Link>
      </form>
    </AuthCard>
  );
}
