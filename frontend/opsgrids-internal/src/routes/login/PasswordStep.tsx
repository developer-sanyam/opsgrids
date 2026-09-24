import { useEffect, useRef, useState } from "react";

import { ApiError, problemMessage, useMutation } from "@opsgrids/client";
import { Button, InlineAlert, Switch, TextField } from "@opsgrids/ui";

import { useOperatorAuth } from "@/auth/context";
import type { OperatorStep } from "@/auth/types";
import { referenceOf } from "@/lib/errors";
import { SignInCard } from "./SignInCard";

/** Dev-build only: Vite replaces `import.meta.env.DEV` with a literal, so production bundles carry
 *  neither the credential strings nor the button below. The account is the one `bin/seed-staff`
 *  creates on a development database, whose password is an obviously fake placeholder. */
const DEMO_CREDENTIALS = import.meta.env.DEV
  ? { email: "owner@opsgrids.local", password: "dev-operator-not-a-real-password" } // pragma: allowlist secret — the seeded dev operator's placeholder, not a real secret
  : null;

/** Never leak which half was wrong — one message covers a bad email, a bad password, a locked or a disabled account. */
const CREDENTIAL_ERROR = "That email or password is incorrect.";

interface PasswordStepProps {
  /** What a later step sent the operator back with — an account locked mid-sign-in. */
  notice?: string | undefined;
  onStep: (step: OperatorStep) => void;
}

/**
 * The first step: the operator's email and password. A correct pair never earns a session — the
 * answer is which second-factor step comes next, and the pre-auth token behind it stays in the
 * provider.
 *
 * Client validation only says a field is empty; the server's own rules (an address, a password of
 * the right length) arrive as field errors and land on the field they name. The entered email
 * survives a failure — and the password does too, because a person retyping a long passphrase
 * after a network blip is how a screen teaches people to disable their password manager.
 */
export function PasswordStep({ notice, onStep }: PasswordStepProps) {
  const { login } = useOperatorAuth();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [emailError, setEmailError] = useState<string | undefined>(undefined);
  const [passwordError, setPasswordError] = useState<string | undefined>(undefined);
  const emailRef = useRef<HTMLInputElement>(null);
  const passwordRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    emailRef.current?.focus();
  }, []);

  const signIn = useMutation((input: { email: string; password: string }) => login(input.email, input.password), {
    onSuccess: (step) => onStep(step),
  });

  function submit() {
    setEmailError(undefined);
    setPasswordError(undefined);
    if (email.trim() === "") {
      setEmailError("Enter your operator email.");
      emailRef.current?.focus();
      return;
    }
    if (password === "") {
      setPasswordError("Enter your password.");
      passwordRef.current?.focus();
      return;
    }
    void signIn.mutate({ email: email.trim(), password });
  }

  const fieldErrors = signIn.fieldErrors;
  const hasFieldErrors = Object.keys(fieldErrors).length > 0;
  const failure = signIn.error;
  let message: string | null = null;
  if (failure && !hasFieldErrors) {
    message =
      failure instanceof ApiError && failure.status === 401
        ? CREDENTIAL_ERROR
        : failure instanceof ApiError && failure.status < 500
          ? problemMessage(failure)
          : `Couldn't sign in. ${problemMessage(failure)}`;
  }

  return (
    <SignInCard
      title="Operator sign-in"
      intro="Staff-only. This console reads across every customer — sign in with your operator credentials."
    >
      <form
        onSubmit={(event) => {
          event.preventDefault();
          submit();
        }}
        noValidate
        className="flex flex-col gap-4"
      >
        {notice ? <InlineAlert tone="danger" title={notice} /> : null}
        {message ? <InlineAlert tone="danger" title={message} reference={referenceOf(failure)} /> : null}

        <TextField
          ref={emailRef}
          label="Operator email"
          type="email"
          autoComplete="username"
          value={email}
          onChange={(event) => setEmail(event.target.value)}
          placeholder="you@opsgrids.com"
          error={emailError ?? fieldErrors.email}
        />
        <TextField
          ref={passwordRef}
          label="Password"
          type={showPassword ? "text" : "password"}
          autoComplete="current-password"
          value={password}
          onChange={(event) => setPassword(event.target.value)}
          error={passwordError ?? fieldErrors.password}
        />
        <Switch label="Show password" checked={showPassword} onChange={setShowPassword} />

        <Button type="submit" loading={signIn.pending} className="w-full">
          {signIn.pending ? "Signing in…" : "Sign in to console"}
        </Button>
      </form>

      {DEMO_CREDENTIALS ? (
        <button
          type="button"
          onClick={() => {
            setEmail(DEMO_CREDENTIALS.email);
            setPassword(DEMO_CREDENTIALS.password);
            setEmailError(undefined);
            setPasswordError(undefined);
            signIn.reset();
          }}
          className="mt-5 w-full rounded-tile bg-accent-soft px-4 py-3 text-left text-xs text-accent-ink transition-colors hover:bg-accent/40"
        >
          <span className="font-bold">Demo access</span>
          <span className="money block text-accent-ink/80">Tap to fill · {DEMO_CREDENTIALS.email}</span>
        </button>
      ) : null}
    </SignInCard>
  );
}
