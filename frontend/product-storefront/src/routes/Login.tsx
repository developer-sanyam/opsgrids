import { useId, useState } from "react";
import type { FormEvent } from "react";
import { Navigate, useLocation, useNavigate } from "react-router-dom";

import { ApiError, useAuth } from "@opsgrids/client";
import type { OrganizationChoice } from "@opsgrids/client";
import { EyeIcon, EyeOffIcon } from "@opsgrids/ui";

import { Button } from "@/ui";
import { LogoMark } from "@/ui/LogoMark";

/** Dev-build only: Vite replaces `import.meta.env.DEV` with a literal, so production
 *  bundles carry neither the credential strings nor the button below. */
const DEMO_CREDENTIALS = import.meta.env.DEV
  ? { email: "storefront@opsgrids-demo.example.com", password: "Demo-opsgrids-2026" } // pragma: allowlist secret — seeded dev-demo credential, not a real secret
  : null;

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

  // Already signed in (e.g. hit /login directly) — go where they were headed.
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
      <main className="grid min-h-dvh place-items-center px-4 py-10">
        <div className="rise w-full max-w-[26rem]">
          <div className="mb-6 flex items-center justify-center gap-2.5">
            <LogoMark className="size-9" />
            <span className="text-[1.15rem] font-extrabold tracking-tight">OpsGrids Storefront</span>
          </div>

          <div className="rounded-card bg-surface p-7 sm:p-8" style={{ boxShadow: "var(--shadow-float)" }}>
            {orgs ? (
              <div className="flex flex-col gap-4">
                <h1 className="display text-2xl font-extrabold">Choose a workspace</h1>
                {error ? (
                  <p role="alert" className="rounded-tile bg-danger-soft px-4 py-3 text-sm font-medium text-danger">
                    {error}
                  </p>
                ) : null}
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
            ) : (
              <>
                <header className="mb-6 flex flex-col gap-1.5">
                  <h1 className="display text-2xl font-extrabold">Sign in to OpsGrids Storefront</h1>
                  <p className="text-sm text-muted">
                    Pick a theme, compose your pages, and publish a store your dealers can buy from.
                  </p>
                </header>

                <form onSubmit={handleSubmit} className="flex flex-col gap-4" noValidate>
                  {error ? (
                    <p role="alert" className="rounded-tile bg-danger-soft px-4 py-3 text-sm font-medium text-danger">
                      {error}
                    </p>
                  ) : null}

                  <div className="flex flex-col gap-1.5">
                    <label htmlFor={emailId} className="text-sm font-semibold text-ink-soft">
                      Work email
                    </label>
                    <input
                      id={emailId}
                      type="email"
                      autoComplete="email"
                      required
                      value={email}
                      onChange={(e) => setEmail(e.target.value)}
                      placeholder="you@company.com"
                      className="h-12 rounded-tile bg-ground px-4 text-sm text-ink placeholder:text-faint focus:outline-none focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ink"
                    />
                  </div>

                  <div className="flex flex-col gap-1.5">
                    <label htmlFor={passwordId} className="text-sm font-semibold text-ink-soft">
                      Password
                    </label>
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
                  </div>

                  <Button type="submit" disabled={submitting}>
                    {submitting ? "Signing in…" : "Sign in"}
                  </Button>

                  {DEMO_CREDENTIALS ? (
                    <button
                      type="button"
                      onClick={() => {
                        setEmail(DEMO_CREDENTIALS.email);
                        setPassword(DEMO_CREDENTIALS.password);
                        setError(null);
                      }}
                      className="text-xs font-semibold text-muted underline-offset-2 hover:text-ink hover:underline"
                    >
                      Use the demo credentials
                    </button>
                  ) : null}
                </form>
              </>
            )}
          </div>
        </div>
      </main>
    </div>
  );
}
