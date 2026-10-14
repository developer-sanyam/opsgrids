import type { ReactNode } from "react";
import { Navigate, useLocation } from "react-router-dom";

import { ConnectionErrorScreen } from "./ConnectionErrorScreen";
import { useAuth } from "./context";
import { useProductChrome } from "./productChrome";

interface RequireAuthProps {
  /** The product's mark, pulsing while the session boots. Defaults to the one the app's
   *  `ProductAuthProvider` declared, so an app does not wrap this just to pass its logo. */
  logo?: ReactNode;
  /** What the hold screen says — "Signing you in…", "Opening the service desk…". Defaults to the
   *  `ProductAuthProvider`'s, then to "Signing you in…". */
  message?: string | undefined;
  children: ReactNode;
}

/** A calm full-screen hold while the stored token is verified on boot. */
function BootScreen({ logo, message }: { logo: ReactNode; message: string }) {
  return (
    <div className="min-h-dvh grid place-items-center bg-ground">
      <div className="flex flex-col items-center gap-4" role="status" aria-live="polite">
        {logo}
        <span className="text-sm font-medium text-muted">{message}</span>
      </div>
    </div>
  );
}

/**
 * Gate for the authenticated app. Holds while the session boots, redirects to `/login` when
 * there is no session (remembering where the user was headed), shows a retry screen when the
 * boot check itself could not complete, and otherwise renders the app.
 *
 * This hides UI; it is not enforcement. The gateway refuses an unauthenticated call regardless.
 *
 * `state.from` carries the *whole* path the user was headed to (pathname, search and hash) —
 * `/login` reads it back to return here after sign-in, whether that is this app's own form or,
 * once redirected further, the dashboard's (`RedirectToCentralLogin`, ADR-0003 addendum).
 */
export function RequireAuth({ logo: ownLogo, message: ownMessage, children }: RequireAuthProps) {
  const { status } = useAuth();
  const chrome = useProductChrome();
  const location = useLocation();
  const logo = ownLogo ?? chrome.mark;
  const message = ownMessage ?? chrome.bootMessage ?? "Signing you in…";

  if (status === "loading") return <BootScreen logo={logo} message={message} />;
  if (status === "error") {
    return <ConnectionErrorScreen logo={logo} onRetry={() => window.location.reload()} />;
  }
  if (status === "unauthenticated") {
    const from = `${location.pathname}${location.search}${location.hash}`;
    return <Navigate to="/login" replace state={{ from }} />;
  }
  return <>{children}</>;
}
