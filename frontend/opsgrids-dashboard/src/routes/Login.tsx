import { useId, useState } from "react";
import type { FormEvent } from "react";
import { Link, Navigate, useLocation, useNavigate } from "react-router-dom";

import { ApiError, useAuth } from "@opsgrids/client";
import type { OrganizationChoice } from "@opsgrids/client";
import { EyeIcon, EyeOffIcon } from "@opsgrids/ui";

import { AuthCard, Button, Field, InlineError, inputClass } from "@/ui";

/** Never leak which half was wrong — one message covers a bad email, bad password, or a 4xx. */
const CREDENTIAL_ERROR = "That email or password is incorrect.";

interface LocationState {
  from?: string;
}

/** The sign-in screen: one card, two labelled fields, one primary action, one inline error. */
export function LoginPage() {
  const { status, login, selectOrg } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();

  const emailId = useId();
  const passwordId = useId();

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [orgs, setOrgs] = useState<OrganizationChoice[] | null>(null);

  const destination = (location.state as LocationState | null)?.from ?? "/";

  if (status === "authenticated") return <Navigate to={destination} replace />;

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    if (submitting) return;
    setError(null);
    setSubmitting(true);
    try {
      const step = await login(email.trim(), password);
      if (step.kind === "complete") {
        navigate(destination, { replace: true });
      } else if (step.kind === "org_selection") {
        setOrgs(step.organizations);
      } else {
        setError("This account needs a second factor, which isn't supported here yet.");
      }
    } catch (err) {
      if (err instanceof ApiError && err.status >= 400 && err.status < 500) {
        setError(CREDENTIAL_ERROR);
      } else {
        const detail = err instanceof Error ? err.message : "Something went wrong.";
        setError(`Couldn't sign in. ${detail}`);
      }
    } finally {
      setSubmitting(false);
    }
  }

  async function handlePickOrg(organizationId: string) {
    setError(null);
    setSubmitting(true);
    try {
      await selectOrg(organizationId);
      navigate(destination, { replace: true });
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
            {orgs.map((org) => (
              <li key={org.organization_id}>
                <button
                  type="button"
                  disabled={submitting}
                  onClick={() => void handlePickOrg(org.organization_id)}
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
