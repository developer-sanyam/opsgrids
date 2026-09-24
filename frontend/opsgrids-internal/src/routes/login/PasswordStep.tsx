import { useEffect, useId, useRef, useState } from "react";
import type { FormEvent } from "react";

import { ApiError } from "@/api/http";
import { useAuth } from "@/auth/context";
import type { OperatorStep } from "@/auth/types";
import { Button } from "@/ui";
import { EyeIcon, EyeOffIcon } from "@/ui/icons";
import { SignInShell } from "./SignInShell";

/** Dev-build only: Vite replaces `import.meta.env.DEV` with a literal, so production bundles carry
 *  neither the credential strings nor the button below. */
const DEMO_CREDENTIALS = import.meta.env.DEV
  ? { email: "priya.venkatesan@opsgrids.com", password: "Operator-demo-2026" } // pragma: allowlist secret — seeded dev-demo credential, not a real secret
  : null;

/** Never leak which half was wrong — one message covers a bad email, a bad password, a locked or a
 *  disabled account (the server itself answers all four identically, for the same reason). */
const CREDENTIAL_ERROR = "That email or password is incorrect.";

interface PasswordStepProps {
  /** What a later step sent the operator back with — an account locked mid-sign-in. */
  notice?: string | undefined;
  onStep: (step: OperatorStep) => void;
}

/**
 * The first step: the operator's email and password. A correct pair never earns a session — the
 * answer is which second-factor step comes next — and the pre-auth token behind it stays inside
 * the provider, never in this screen's state.
 *
 * The entered email and password both survive a failed attempt: a person retyping a long
 * passphrase after a dropped connection is how a screen teaches people to disable their password
 * manager.
 */
export function PasswordStep({ notice, onStep }: PasswordStepProps) {
  const { login } = useAuth();
  const emailId = useId();
  const passwordId = useId();

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const emailRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    emailRef.current?.focus();
  }, []);

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      const step = await login(email.trim(), password);
      onStep(step);
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
    if (!DEMO_CREDENTIALS) return;
    setEmail(DEMO_CREDENTIALS.email);
    setPassword(DEMO_CREDENTIALS.password);
    setError(null);
  }

  return (
    <SignInShell
      title="Operator sign-in"
      intro="Staff-only. This console reads across every customer — sign in with your operator credentials."
    >
      <form onSubmit={handleSubmit} className="flex flex-col gap-4" noValidate>
        {notice ? (
          <p role="alert" className="rounded-tile bg-suspended-soft px-4 py-3 text-sm font-medium text-suspended">
            {notice}
          </p>
        ) : null}
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
            ref={emailRef}
            type="email"
            autoComplete="username"
            required
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            placeholder="you@opsgrids.com"
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

      {DEMO_CREDENTIALS ? (
        <button
          type="button"
          onClick={fillDemo}
          className="mt-5 w-full rounded-tile bg-accent-soft px-4 py-3 text-left text-xs text-accent-ink transition-colors hover:bg-accent/40"
        >
          <span className="font-bold">Demo access</span>
          <span className="mono block text-accent-ink/80">Tap to fill · {DEMO_CREDENTIALS.email}</span>
        </button>
      ) : null}
    </SignInShell>
  );
}
