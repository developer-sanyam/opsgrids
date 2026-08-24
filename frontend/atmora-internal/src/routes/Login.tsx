import { useId, useState } from "react";
import { Navigate, useLocation, useNavigate } from "react-router-dom";

import { ApiError } from "@/api/http";
import { useAuth } from "@/auth/context";
import { Button } from "@/ui";
import { EyeIcon, EyeOffIcon, LogoMark } from "@/ui/icons";

const DEMO_EMAIL = "priya.venkatesan@atmora.com";
const DEMO_PASSWORD = "Operator-demo-2026"; // pragma: allowlist secret — seeded dev-demo credential, not a real secret

/** Never leak which half was wrong — one message covers a bad email, bad password, or a 4xx. */
const CREDENTIAL_ERROR = "That email or password is incorrect.";

interface LocationState {
  from?: string;
}

/**
 * The operator sign-in screen: a single card on the control-panel grid field under a soft accent
 * glow, clearly marked "Internal" so staff never confuse it with a customer login. Two labelled
 * fields, an accent primary action, and an inline error. Wiring to the live staff auth endpoint
 * (ADR-0004 P6a) is a one-line change in `AuthProvider`.
 */
export function LoginPage() {
  const { status, login } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();

  const emailId = useId();
  const passwordId = useId();

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const destination = (location.state as LocationState | null)?.from ?? "/";

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
      } else {
        setError("This staff account needs a second factor, which isn't supported here yet.");
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

  function fillDemo() {
    setEmail(DEMO_EMAIL);
    setPassword(DEMO_PASSWORD);
    setError(null);
  }

  return (
    <div className="relative min-h-dvh overflow-hidden bg-ground">
      {/* Atmosphere: an accent glow over the console's engineering grid. */}
      <div aria-hidden className="pointer-events-none absolute inset-0">
        <div className="grid-field absolute inset-0 opacity-[0.5]" style={{
          maskImage: "radial-gradient(circle at 50% 34%, black, transparent 70%)",
          WebkitMaskImage: "radial-gradient(circle at 50% 34%, black, transparent 70%)",
        }} />
        <div
          className="absolute left-1/2 top-[-14rem] size-[40rem] -translate-x-1/2 rounded-full"
          style={{ background: "radial-gradient(closest-side, rgba(108,76,241,0.22), transparent)" }}
        />
        <div
          className="absolute bottom-[-12rem] right-[-8rem] size-[30rem] rounded-full"
          style={{ background: "radial-gradient(closest-side, rgba(108,76,241,0.12), transparent)" }}
        />
      </div>

      <main className="relative grid min-h-dvh place-items-center px-4 py-10">
        <div className="rise w-full max-w-[26rem]">
          <div className="mb-6 flex items-center justify-center gap-2.5">
            <LogoMark className="size-9" />
            <span className="text-[1.15rem] font-extrabold tracking-tight">Atmora</span>
            <span className="mono rounded-md bg-accent-soft px-2 py-0.5 text-[0.65rem] font-semibold uppercase tracking-widest text-accent-ink">
              Internal
            </span>
          </div>

          <div className="rounded-card bg-surface p-7 sm:p-8" style={{ boxShadow: "var(--shadow-float)" }}>
            <header className="mb-6 flex flex-col gap-1.5">
              <h1 className="display text-2xl font-extrabold">Operator sign-in</h1>
              <p className="text-sm text-muted">
                Staff-only. This console reads across every customer — sign in with your operator
                credentials.
              </p>
            </header>

            <form onSubmit={handleSubmit} className="flex flex-col gap-4" noValidate>
              {error ? (
                <p role="alert" className="rounded-tile bg-suspended-soft px-4 py-3 text-sm font-medium text-suspended">
                  {error}
                </p>
              ) : null}

              <div className="flex flex-col gap-1.5">
                <label htmlFor={emailId} className="text-sm font-semibold text-ink-soft">
                  Operator email
                </label>
                <input
                  id={emailId}
                  type="email"
                  autoComplete="email"
                  required
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  placeholder="you@atmora.com"
                  className="h-12 rounded-tile bg-ground px-4 text-sm text-ink placeholder:text-faint focus:outline-none focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2"
                />
              </div>

              <div className="flex flex-col gap-1.5">
                <label htmlFor={passwordId} className="text-sm font-semibold text-ink-soft">
                  Password
                </label>
                <div className="flex items-center rounded-tile bg-ground pr-1.5 focus-within:outline-2 focus-within:outline focus-within:outline-accent focus-within:outline-offset-2">
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
                    {showPassword ? <EyeOffIcon className="size-5" /> : <EyeIcon className="size-5" />}
                  </button>
                </div>
              </div>

              <Button type="submit" size="md" disabled={submitting} className="mt-1 w-full">
                {submitting ? "Signing in…" : "Sign in to console"}
              </Button>
            </form>

            <button
              type="button"
              onClick={fillDemo}
              className="mt-5 w-full rounded-tile bg-accent-soft px-4 py-3 text-left text-xs text-accent-ink transition-colors hover:bg-accent/40"
            >
              <span className="font-bold">Demo access</span>
              <span className="mono block text-accent-ink/80">Tap to fill · {DEMO_EMAIL}</span>
            </button>
          </div>

          <p className="mt-6 text-center text-xs text-faint">
            Atmora staff console · access is logged and audited
          </p>
        </div>
      </main>
    </div>
  );
}
