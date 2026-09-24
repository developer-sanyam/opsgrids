import type { ReactNode } from "react";

export interface ConnectionErrorScreenProps {
  /** The product's mark. */
  logo: ReactNode;
  message?: string | undefined;
  /** Reloading the page is deliberately the only retry this offers: it re-runs the whole boot
   *  sequence (a fresh silent-refresh attempt included) rather than this component trying to
   *  poke a specific piece of provider state it does not own. */
  onRetry: () => void;
}

/**
 * Shown instead of a login redirect when the session's own status genuinely cannot be
 * determined — a network failure, or a cross-origin refresh call CORS refused outright — rather
 * than a definite "you are signed out" from the server. `AuthProvider`'s `status === "error"`
 * is what this renders for, in both `RequireAuth` and `RedirectToCentralLogin`: redirecting to
 * `/login` here would be a guess, and when the guess is wrong on a systemic failure (a
 * misconfigured CORS entry for this one app, say) it is the guess that turns into a redirect
 * loop between this app and the dashboard.
 */
export function ConnectionErrorScreen({
  logo,
  message = "Couldn't reach the sign-in service. Check your connection and try again.",
  onRetry,
}: ConnectionErrorScreenProps) {
  return (
    <div className="min-h-dvh grid place-items-center bg-ground">
      <div className="flex max-w-sm flex-col items-center gap-4 text-center" role="alert">
        {logo}
        <p className="text-sm font-medium text-ink">{message}</p>
        <button
          type="button"
          onClick={onRetry}
          className="rounded-tile bg-ink px-4 py-2 text-sm font-semibold text-surface transition-colors hover:opacity-90 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ink"
        >
          Retry
        </button>
      </div>
    </div>
  );
}
