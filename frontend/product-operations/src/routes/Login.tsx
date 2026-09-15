import { useId, useState } from "react";
import { Navigate, useLocation, useNavigate } from "react-router-dom";

import { ApiError } from "@opsgrids/client";
import { useAuth } from "@opsgrids/client";
import type { OrganizationChoice } from "@opsgrids/client";
import { Button } from "@/ui";
import { EyeIcon, EyeOffIcon } from "@opsgrids/ui";
import { LogoMark } from "@/ui/LogoMark";

/** Dev-build only: Vite replaces `import.meta.env.DEV` with a literal, so production
 *  bundles carry neither the credential strings nor the button below. */
const DEMO_CREDENTIALS = import.meta.env.DEV
  ? { email: "ops@opsgrids-demo.example.com", password: "Demo-opsgrids-2026" } // pragma: allowlist secret — seeded dev-demo credential, not a real secret
  : null;

/** Never leak which half was wrong — one message covers a bad email, bad password, or a 4xx. */
const CREDENTIAL_ERROR = "That email or password is incorrect.";

interface LocationState {
  from?: string;
}

/**
 * The sign-in screen: a single card floating on the light-grey ground under a soft accent glow.
 * Two labelled fields, an accent primary action, an inline error, and — when a user belongs to
 * more than one organization — an org picker. The demo hint fills the fields with one click.
 */
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
  if (status === "authenticated") {
    return <Navigate to={destination} replace />;
  }

  async function handleSubmit(event: React.FormEvent) {
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

  function fillDemo() {
    if (!DEMO_CREDENTIALS) return;
    setEmail(DEMO_CREDENTIALS.email);
    setPassword(DEMO_CREDENTIALS.password);
    setError(null);
  }

  return (
    <div className="relative min-h-dvh overflow-hidden bg-ground">
      {/* Atmosphere: two accent glows over a whisper-faint grid. */}
      <div aria-hidden className="pointer-events-none absolute inset-0">
        <div
          className="absolute left-1/2 top-[-14rem] size-[40rem] -translate-x-1/2 rounded-full blur-[2px]"
          style={{ background: "radial-gradient(closest-side, rgba(108,76,241,0.22), transparent)" }}
        />
        <div
          className="absolute bottom-[-12rem] right-[-8rem] size-[30rem] rounded-full"
          style={{ background: "radial-gradient(closest-side, rgba(108,76,241,0.12), transparent)" }}
        />
        <div
          className="absolute inset-0 opacity-[0.5]"
          style={{
            backgroundImage:
              "linear-gradient(var(--color-line) 1px, transparent 1px), linear-gradient(90deg, var(--color-line) 1px, transparent 1px)",
            backgroundSize: "42px 42px",
            maskImage: "radial-gradient(circle at 50% 38%, black, transparent 72%)",
            WebkitMaskImage: "radial-gradient(circle at 50% 38%, black, transparent 72%)",
          }}
        />
      </div>

      <main className="relative grid min-h-dvh place-items-center px-4 py-10">
        <div className="rise w-full max-w-[26rem]">
          {/* Brand */}
          <div className="mb-6 flex items-center justify-center gap-2.5">
            <LogoMark className="size-9" />
            <span className="text-[1.15rem] font-extrabold tracking-tight">OpsGrids Operations</span>
          </div>

          <div
            className="rounded-card bg-surface p-7 sm:p-8"
            style={{ boxShadow: "var(--shadow-float)" }}
          >
            {orgs ? (
              <OrgPicker
                orgs={orgs}
                disabled={submitting}
                error={error}
                onPick={handlePickOrg}
              />
            ) : (
              <>
                <header className="mb-6 flex flex-col gap-1.5">
                  <h1 className="display text-2xl font-extrabold">Sign in to Operations</h1>
                  <p className="text-sm text-muted">
                    Procurement, inventory and the paperwork between them.
                  </p>
                </header>

                <form onSubmit={handleSubmit} className="flex flex-col gap-4" noValidate>
                  {error ? (
                    <p
                      role="alert"
                      className="rounded-tile bg-danger-soft px-4 py-3 text-sm font-medium text-danger"
                    >
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
                      className="h-12 rounded-tile bg-ground px-4 text-sm text-ink placeholder:text-faint focus:outline-none focus-visible:outline-2 focus-visible:outline-ink focus-visible:outline-offset-2"
                    />
                  </div>

                  <div className="flex flex-col gap-1.5">
                    <label htmlFor={passwordId} className="text-sm font-semibold text-ink-soft">
                      Password
                    </label>
                    <div className="flex items-center rounded-tile bg-ground pr-1.5 focus-within:outline-2 focus-within:outline focus-within:outline-ink focus-within:outline-offset-2">
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
                        className="grid size-9 place-items-center rounded-full text-muted hover:bg-line hover:text-ink transition-colors"
                      >
                        {showPassword ? (
                          <EyeOffIcon className="size-5" />
                        ) : (
                          <EyeIcon className="size-5" />
                        )}
                      </button>
                    </div>
                  </div>

                  <Button
                    type="submit"
                    size="md"
                    disabled={submitting}
                    className="mt-1 w-full"
                  >
                    {submitting ? "Signing in…" : "Sign in"}
                  </Button>
                </form>

                {DEMO_CREDENTIALS && (
                  <button
                    type="button"
                    onClick={fillDemo}
                    className="mt-5 w-full rounded-tile bg-accent-soft px-4 py-3 text-left text-xs text-accent-ink transition-colors hover:bg-accent/40"
                  >
                    <span className="font-bold">Demo access</span>
                    <span className="block text-accent-ink/80">
                      Tap to fill · {DEMO_CREDENTIALS.email}
                    </span>
                  </button>
                )}
              </>
            )}
          </div>

          <p className="mt-6 text-center text-xs text-faint">
            OpsGrids Operations · secured by your organization's single sign-on
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
      <header className="flex flex-col gap-1.5">
        <h1 className="display text-2xl font-extrabold">Choose an organization</h1>
        <p className="text-sm text-muted">
          You belong to more than one. Pick the one to sign into.
        </p>
      </header>

      {error ? (
        <p
          role="alert"
          className="rounded-tile bg-danger-soft px-4 py-3 text-sm font-medium text-danger"
        >
          {error}
        </p>
      ) : null}

      <ul className="flex flex-col gap-2">
        {orgs.map((org) => (
          <li key={org.organization_id}>
            <button
              type="button"
              disabled={disabled}
              onClick={() => onPick(org.organization_id)}
              className="flex w-full items-center gap-3 rounded-tile bg-ground px-4 py-3 text-left transition-colors hover:bg-line disabled:cursor-not-allowed disabled:opacity-60"
            >
              <span className="grid size-9 place-items-center rounded-full bg-ink text-xs font-bold text-white">
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
