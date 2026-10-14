import { useEffect, useRef } from "react";
import type { FormEvent, ReactNode } from "react";

import { Button } from "./Button";
import { InlineAlert } from "./InlineAlert";
import { PasswordField } from "./PasswordField";
import { TextField } from "./TextField";

/** An organization the person can sign into, as the sign-in step offers it. */
export interface LoginOrganization {
  organization_id: string;
  name: string;
  roles: readonly string[];
}

interface ProductLoginScreenProps {
  /** The product's name as the brand line reads it: "OpsGrids Finance". */
  brand: string;
  /** The product's mark, beside the brand line. */
  logo: ReactNode;
  /** One sentence under the heading saying what the product is for. */
  tagline: string;
  /** What follows "Sign in to" in the heading, when it is not the brand. */
  headingName?: string | undefined;
  /** What the footer calls the product, when it is not the brand. */
  footerName?: string | undefined;
  /** Which step the person is on. The organization step lists `organizations` to pick from. */
  step: "credentials" | "organizations";
  organizations?: readonly LoginOrganization[] | undefined;
  email: string;
  onEmailChange: (value: string) => void;
  password: string;
  onPasswordChange: (value: string) => void;
  /** A credentials or organization request is in flight: the controls disable and say so. */
  submitting: boolean;
  /** The failure to show, already worded. Announced when it appears. */
  error: string | null;
  onSubmit: () => void;
  onPickOrganization: (organizationId: string) => void;
  /** A dev-only shortcut that fills the fields. Omit it in a production build. */
  demo?: { email: string; onFill: () => void } | undefined;
}

/**
 * The dev-mode sign-in screen the standalone product apps share: a card on the ground with two
 * labelled fields, one primary action, an inline error and, for a person who belongs to more than
 * one organization, a picker.
 *
 * Every product but the central dashboard signs in through the dashboard in production; this
 * screen is what `npm run dev` shows, so the nine copies that used to differ only in a name and a
 * sentence are one component. Moving between steps moves focus to the new heading, so a keyboard
 * or screen-reader user lands where the content changed instead of on a control that has gone.
 */
export function ProductLoginScreen({
  brand,
  logo,
  tagline,
  headingName,
  footerName,
  step,
  organizations = [],
  email,
  onEmailChange,
  password,
  onPasswordChange,
  submitting,
  error,
  onSubmit,
  onPickOrganization,
  demo,
}: ProductLoginScreenProps) {
  const headingRef = useRef<HTMLHeadingElement>(null);
  const previousStep = useRef(step);

  useEffect(() => {
    if (previousStep.current === step) return;
    previousStep.current = step;
    headingRef.current?.focus();
  }, [step]);

  function handleSubmit(event: FormEvent) {
    event.preventDefault();
    if (!submitting) onSubmit();
  }

  const heading = step === "organizations" ? "Choose an organization" : `Sign in to ${headingName ?? brand}`;
  const intro =
    step === "organizations" ? "You belong to more than one. Pick the one to sign into." : tagline;

  return (
    <div className="min-h-dvh bg-ground">
      <main className="grid min-h-dvh place-items-center px-4 py-12">
        <div className="rise w-full max-w-md">
          <div className="mb-6 flex items-center justify-center gap-3">
            {logo}
            <span className="text-section font-bold tracking-tight">{brand}</span>
          </div>

          <div className="rounded-card bg-surface p-6 shadow-float sm:p-8">
            <header className="mb-6 flex flex-col gap-2">
              <h1 ref={headingRef} tabIndex={-1} className="display text-page-sm text-balance focus:outline-none">
                {heading}
              </h1>
              <p className="text-sm text-muted">{intro}</p>
            </header>

            {error ? <InlineAlert tone="danger" title={error} className="mb-4" /> : null}

            {step === "organizations" ? (
              <ul className="flex list-none flex-col gap-2 p-0">
                {organizations.map((org) => (
                  <li key={org.organization_id}>
                    <button
                      type="button"
                      disabled={submitting}
                      onClick={() => onPickOrganization(org.organization_id)}
                      className="flex min-h-14 w-full items-center gap-3 rounded-tile border border-field-edge bg-surface px-4 py-3 text-left transition-colors hover:bg-sunken disabled:cursor-not-allowed disabled:opacity-60"
                    >
                      <span
                        aria-hidden
                        className="grid size-9 shrink-0 place-items-center rounded-full bg-ink text-caption font-bold text-surface"
                      >
                        {org.name.slice(0, 2).toUpperCase()}
                      </span>
                      <span className="flex min-w-0 flex-col leading-tight">
                        <span className="truncate font-semibold text-ink">{org.name}</span>
                        <span className="truncate text-caption text-muted">{org.roles.join(" · ")}</span>
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
            ) : (
              <>
                <form onSubmit={handleSubmit} className="flex flex-col gap-4" noValidate>
                  <TextField
                    label="Work email"
                    type="email"
                    autoComplete="email"
                    required
                    value={email}
                    onChange={(event) => onEmailChange(event.target.value)}
                    placeholder="you@company.com"
                  />
                  <PasswordField value={password} onChange={onPasswordChange} placeholder="••••••••" />
                  <Button type="submit" loading={submitting} className="mt-1 w-full">
                    {submitting ? "Signing in…" : "Sign in"}
                  </Button>
                </form>

                {demo ? (
                  <button
                    type="button"
                    onClick={demo.onFill}
                    className="mt-6 flex min-h-11 w-full flex-col justify-center rounded-tile bg-accent-soft px-4 py-3 text-left text-caption text-accent-ink transition-colors hover:bg-accent-soft/70"
                  >
                    <span className="font-bold">Demo access</span>
                    <span>Tap to fill · {demo.email}</span>
                  </button>
                ) : null}
              </>
            )}
          </div>

          <p className="mt-6 text-center text-caption text-muted">
            {footerName ?? brand} · secured by your organization's single sign-on
          </p>
        </div>
      </main>
    </div>
  );
}
