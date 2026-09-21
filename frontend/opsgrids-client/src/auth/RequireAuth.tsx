import type { ReactNode } from "react";
import { Navigate, useLocation } from "react-router-dom";

import { useAuth } from "./context";

interface RequireAuthProps {
  /** The product's mark, pulsing while the session boots. */
  logo: ReactNode;
  /** What the hold screen says — "Signing you in…", "Opening the service desk…". */
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
 * there is no session (remembering where the user was headed), and otherwise renders the app.
 *
 * This hides UI; it is not enforcement. The gateway refuses an unauthenticated call regardless.
 */
export function RequireAuth({ logo, message = "Signing you in…", children }: RequireAuthProps) {
  const { status } = useAuth();
  const location = useLocation();

  if (status === "loading") return <BootScreen logo={logo} message={message} />;
  if (status === "unauthenticated") {
    return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  }
  return <>{children}</>;
}
