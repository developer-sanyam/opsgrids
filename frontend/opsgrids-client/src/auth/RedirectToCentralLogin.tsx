import type { ReactNode } from "react";
import { useEffect, useState } from "react";
import { Navigate, useLocation } from "react-router-dom";

import { ConnectionErrorScreen } from "./ConnectionErrorScreen";
import { useAuth } from "./context";
import { normalizeOrigin } from "./origin";

export interface RedirectToCentralLoginProps {
  /** The dashboard's origin, no trailing slash — `https://app.opsgrids.com` in production. */
  authOrigin: string;
  /** The product's mark, shown while the browser is sent on its way. */
  logo: ReactNode;
  message?: string | undefined;
  /** Named in the loop-breaker's screen: "Couldn't sign you in to {appName}". */
  appName?: string | undefined;
}

/** A wanted path that is itself `/login` — a stale bookmark, a manually edited URL, or (this is
 *  the bug this whole file exists to close) the *previous* value of `window.location.href`
 *  mistaken for one — must never become the value sent back as `next`. That is the redirect
 *  loop's root cause reached by a different route: this component always sends `?next=` to the
 *  page the user actually wanted (`location.state.from`, set by `RequireAuth`), never to
 *  wherever the browser happens to be sitting when this component itself renders. */
function isLoginPath(path: string): boolean {
  return /^\/login(?:[/?#]|$)/i.test(path);
}

const HANDOFF_KEY = "opsgrids.sso_handoff_at";
const HANDOFF_WINDOW_MS = 60_000;
const HANDOFF_LIMIT = 2;

function recentHandoffTimes(): number[] {
  try {
    const raw = sessionStorage.getItem(HANDOFF_KEY);
    const parsed: unknown = raw ? JSON.parse(raw) : [];
    if (!Array.isArray(parsed)) return [];
    const now = Date.now();
    return parsed.filter(
      (value): value is number => typeof value === "number" && now - value < HANDOFF_WINDOW_MS,
    );
  } catch {
    return [];
  }
}

function recordHandoff(): void {
  try {
    const kept = recentHandoffTimes();
    kept.push(Date.now());
    sessionStorage.setItem(HANDOFF_KEY, JSON.stringify(kept));
  } catch {
    // Best effort: the worst case is the breaker under-counting, not a crash.
  }
}

function clearHandoffs(): void {
  try {
    sessionStorage.removeItem(HANDOFF_KEY);
  } catch {
    // Best effort.
  }
}

/** Shown after two hand-offs to the dashboard inside a minute — the SSO loop breaker. Reaching
 *  this means the dashboard keeps reporting signed in and sending the browser straight back
 *  here, which the wanted-page fix and the `/login`-path guard above should already prevent for
 *  the causes known today; this is the backstop for a cause that is not. */
function SsoHandoffBlockedScreen({
  logo,
  appName,
  authOrigin,
  onRetry,
}: {
  logo: ReactNode;
  appName: string;
  authOrigin: string;
  onRetry: () => void;
}) {
  return (
    <div className="min-h-dvh grid place-items-center bg-ground">
      <div className="flex max-w-sm flex-col items-center gap-4 text-center" role="alert">
        {logo}
        <p className="text-sm font-medium text-ink">Couldn't sign you in to {appName}.</p>
        <div className="flex gap-3">
          <button
            type="button"
            onClick={onRetry}
            className="rounded-tile bg-ink px-4 py-2 text-sm font-semibold text-surface transition-colors hover:opacity-90 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ink"
          >
            Try again
          </button>
          <button
            type="button"
            onClick={() => window.location.assign(authOrigin)}
            className="rounded-tile border border-line px-4 py-2 text-sm font-semibold text-ink transition-colors hover:bg-surface focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ink"
          >
            Open dashboard
          </button>
        </div>
      </div>
    </div>
  );
}

/**
 * A product app's own `/login` route, everywhere except the dashboard itself and the storefront
 * buyer bundle (ADR-0003 addendum): sign-in only ever happens on the dashboard, which is the
 * one host the refresh cookie lives on.
 *
 * Status-aware, because the wrong reaction to any status but a definite "signed out" is exactly
 * what turned this into a redirect loop:
 *   - `loading` — the boot check (including its own silent-refresh attempt) has not resolved
 *     yet; hold. Rendering the redirect here regardless of what boot finds is bug B1 half of
 *     this file exists to close — a refresh that succeeds must never reach the dashboard at all.
 *   - `authenticated` — the boot check found a session after all (a successful silent refresh);
 *     go straight to the page the user wanted, in this app, with an ordinary internal
 *     `<Navigate>` — never a `window.location.assign` anywhere near the dashboard.
 *   - `error` — the check itself could not complete (network, or CORS refusing a cross-origin
 *     refresh outright). Unknown is not the same as signed out: show a retry screen rather than
 *     bounce to the dashboard, which — unaffected by whatever broke this app's own refresh —
 *     reports signed in and sends the browser straight back.
 *   - `unauthenticated` — the one status this component actually redirects for, and only after
 *     the hand-off breaker below confirms this is not the third redirect in a minute.
 *
 * `next` is always built from `location.state.from` — the page `RequireAuth` was protecting,
 * carried through React Router state — never from `window.location.href`. By the time this
 * component renders, the browser is already sitting on `/login`; sending that back as `next`
 * is precisely how the dashboard was made to bounce here again after every successful sign-in.
 */
export function RedirectToCentralLogin({
  authOrigin,
  logo,
  message = "Taking you to sign in…",
  appName = "this app",
}: RedirectToCentralLoginProps) {
  const { status } = useAuth();
  const location = useLocation();
  const from = (location.state as { from?: string } | null)?.from;
  const [blocked, setBlocked] = useState(false);
  const origin = normalizeOrigin(authOrigin);

  useEffect(() => {
    if (status !== "unauthenticated") return;
    if (recentHandoffTimes().length >= HANDOFF_LIMIT) {
      setBlocked(true);
      return;
    }
    const wanted = from && !isLoginPath(from) ? from : "/";
    const next = new URL(wanted, window.location.origin).href;
    recordHandoff();
    window.location.assign(`${origin}/login?next=${encodeURIComponent(next)}`);
  }, [status, from, origin]);

  if (status === "authenticated") {
    const wanted = from && !isLoginPath(from) ? from : "/";
    return <Navigate to={wanted} replace />;
  }
  if (status === "error") {
    return <ConnectionErrorScreen logo={logo} onRetry={() => window.location.reload()} />;
  }
  if (blocked) {
    return (
      <SsoHandoffBlockedScreen
        logo={logo}
        appName={appName}
        authOrigin={origin}
        onRetry={() => {
          clearHandoffs();
          setBlocked(false);
          window.location.reload();
        }}
      />
    );
  }

  // `loading`, and `unauthenticated` for the instant before the effect above navigates away.
  return (
    <div className="min-h-dvh grid place-items-center bg-ground">
      <div className="flex flex-col items-center gap-4" role="status" aria-live="polite">
        {logo}
        <span className="text-sm font-medium text-muted">{message}</span>
      </div>
    </div>
  );
}
