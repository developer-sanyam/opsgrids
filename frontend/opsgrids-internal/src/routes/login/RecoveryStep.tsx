import { useEffect, useRef, useState } from "react";

import { useAuth } from "@/auth/context";
import { Button } from "@/ui";
import { SignInShell } from "./SignInShell";

/**
 * The recovery codes, shown once. They are stored hashed and cannot be shown again, and each one
 * signs in once if the authenticator is lost — so this is the only time they can be saved.
 *
 * The session enrolment minted is already live server-side, but the provider keeps it un-adopted
 * until `finishEnrolment` runs: the redirect a live session triggers would otherwise take this
 * screen away before anyone could read the codes. Focus lands on the heading, which says what this
 * screen is; the codes sit in a plain, selectable list, and nothing here writes them anywhere.
 */
export function RecoveryStep({ recoveryCodes }: { recoveryCodes: string[] }) {
  const { finishEnrolment } = useAuth();
  const [saved, setSaved] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const headingRef = useRef<HTMLHeadingElement>(null);

  useEffect(() => {
    headingRef.current?.focus();
  }, []);

  async function finish() {
    setError(null);
    setSubmitting(true);
    try {
      await finishEnrolment();
      // The redirect on success lives in LoginPage, driven by `status` — nothing to do here.
    } catch (err) {
      const detail = err instanceof Error ? err.message : "Something went wrong.";
      setError(`Couldn't finish signing in. ${detail}`);
      setSubmitting(false);
    }
  }

  return (
    <SignInShell
      title="Save your recovery codes"
      titleRef={headingRef}
      intro="Each code signs you in once if you lose access to your authenticator app."
    >
      <div className="flex flex-col gap-4">
        <p role="alert" className="rounded-tile bg-pastdue-soft px-4 py-3 text-sm font-semibold text-pastdue">
          Shown only once — save these somewhere safe.
        </p>

        <ul className="mono grid grid-cols-1 gap-2 rounded-tile bg-ground p-4 text-sm text-ink sm:grid-cols-2">
          {recoveryCodes.map((code) => (
            <li key={code} className="select-all">
              {code}
            </li>
          ))}
        </ul>

        {error ? (
          <p role="alert" className="rounded-tile bg-suspended-soft px-4 py-3 text-sm font-medium text-suspended">
            {error}
          </p>
        ) : null}

        <label className="inline-flex items-start gap-2.5 text-sm font-semibold text-ink-soft">
          <input
            type="checkbox"
            checked={saved}
            onChange={(e) => setSaved(e.target.checked)}
            className="mt-0.5 size-4 accent-[var(--color-accent)]"
          />
          I&apos;ve saved these codes
        </label>

        <Button size="md" disabled={!saved || submitting} onClick={() => void finish()} className="w-full">
          {submitting ? "Signing in…" : "Continue to the console"}
        </Button>
      </div>
    </SignInShell>
  );
}
