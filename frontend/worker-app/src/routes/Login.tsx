import { useState } from "react";
import type { FormEvent } from "react";
import { Navigate, useLocation, useNavigate } from "react-router-dom";

import { ApiError, useAuth } from "@opsgrids/client";
import type { OrganizationChoice } from "@opsgrids/client";
import { Button, InlineAlert, TextField } from "@opsgrids/ui";

import { LogoMark } from "@/ui/LogoMark";

/** Never leak which half was wrong — one message covers a bad email, bad password, or a 4xx. */
const CREDENTIAL_ERROR = "That email or password is incorrect.";

interface LocationState {
  from?: string;
}

/**
 * The sign-in screen: this app's own `/login`, used everywhere `VITE_AUTH_ORIGIN` is unset (dev,
 * same-origin) — in production every other product app hands sign-in to the dashboard instead
 * (`RedirectToCentralLogin` in `main.tsx`), and this app follows the same rule.
 *
 * A single card on the ground, mobile-first: full width up to a phone's own reading measure, no
 * desktop-dense side rail. When a worker belongs to more than one organization, an org picker
 * follows sign-in, same as every other product app.
 */
export function LoginPage() {
  const { status, login, selectOrg } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [orgs, setOrgs] = useState<OrganizationChoice[] | null>(null);

  const destination = (location.state as LocationState | null)?.from ?? "/";

  // Already signed in (e.g. hit /login directly) — go where they were headed.
  if (status === "authenticated") {
    return <Navigate to={destination} replace />;
  }

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
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
      // A 4xx is bad credentials; anything else is a real fault worth surfacing plainly.
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

  return (
    <div className="min-h-dvh bg-ground">
      <main className="grid min-h-dvh place-items-center px-4 py-8">
        <div className="rise w-full max-w-sm">
          <div className="mb-6 flex items-center justify-center gap-2">
            <LogoMark className="size-9" />
            <span className="text-base font-extrabold tracking-tight text-ink">OpsGrids Worker</span>
          </div>

          <div className="rounded-card bg-surface p-6 shadow-card sm:p-8">
            {orgs ? (
              <OrgPicker orgs={orgs} disabled={submitting} error={error} onPick={(id) => void handlePickOrg(id)} />
            ) : (
              <>
                <header className="mb-6 flex flex-col gap-1">
                  <h1 className="text-section font-extrabold">Sign in to Worker</h1>
                  <p className="text-sm text-muted">Pick, pack and dispatch — from your phone.</p>
                </header>

                <form onSubmit={(event) => void handleSubmit(event)} className="flex flex-col gap-4" noValidate>
                  {error ? <InlineAlert tone="danger" title={error} /> : null}

                  <TextField
                    label="Work email"
                    type="email"
                    autoComplete="email"
                    required
                    value={email}
                    onChange={(event) => setEmail(event.target.value)}
                    placeholder="you@company.com"
                  />

                  <TextField
                    label="Password"
                    type="password"
                    autoComplete="current-password"
                    required
                    value={password}
                    onChange={(event) => setPassword(event.target.value)}
                    placeholder="••••••••"
                  />

                  <Button type="submit" size="md" loading={submitting} className="mt-1 w-full">
                    Sign in
                  </Button>
                </form>
              </>
            )}
          </div>

          <p className="mt-6 text-center text-xs text-faint">
            OpsGrids Worker · secured by your organization's single sign-on
          </p>
        </div>
      </main>
    </div>
  );
}

/** The multi-org branch: pick which organization to sign into. */
function OrgPicker({
  orgs,
  disabled,
  error,
  onPick,
}: {
  orgs: OrganizationChoice[];
  disabled: boolean;
  error: string | null;
  onPick: (organizationId: string) => void;
}) {
  return (
    <div className="flex flex-col gap-4">
      <header className="flex flex-col gap-1">
        <h1 className="text-section font-extrabold">Choose an organization</h1>
        <p className="text-sm text-muted">You belong to more than one. Pick the one to sign into.</p>
      </header>

      {error ? <InlineAlert tone="danger" title={error} /> : null}

      <ul className="flex flex-col gap-2">
        {orgs.map((org) => (
          <li key={org.organization_id}>
            <button
              type="button"
              disabled={disabled}
              onClick={() => onPick(org.organization_id)}
              className="flex h-14 w-full items-center gap-3 rounded-tile bg-ground px-4 text-left transition-colors hover:bg-sunken disabled:cursor-not-allowed disabled:opacity-60"
            >
              <span className="grid size-9 shrink-0 place-items-center rounded-full bg-ink text-xs font-bold text-surface">
                {org.name.slice(0, 2).toUpperCase()}
              </span>
              <span className="flex min-w-0 flex-col leading-tight">
                <span className="truncate font-semibold text-ink">{org.name}</span>
                <span className="truncate text-xs text-muted">{org.roles.join(" · ")}</span>
              </span>
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}
