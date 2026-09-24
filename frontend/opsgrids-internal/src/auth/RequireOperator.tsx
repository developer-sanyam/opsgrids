import type { ReactNode } from "react";
import { Navigate, useLocation } from "react-router-dom";

import { LogoMark } from "@/ui/LogoMark";
import { useOperatorAuth } from "./context";

/** A calm full-screen hold while the stored token is verified on boot. */
function BootScreen() {
  return (
    <div className="min-h-dvh grid place-items-center bg-ground">
      <div className="flex flex-col items-center gap-4" role="status" aria-live="polite">
        <LogoMark className="size-11 animate-pulse" />
        <span className="text-sm font-medium text-muted">Verifying operator session…</span>
      </div>
    </div>
  );
}

/**
 * Gate for the authenticated console. Holds while the session boots, redirects to `/login` when
 * there is no session (remembering where the operator was headed, query included), and otherwise
 * renders the app.
 *
 * This hides UI; it is not enforcement. The gateway refuses an unauthenticated call regardless.
 */
export function RequireOperator({ children }: { children: ReactNode }) {
  const { status } = useOperatorAuth();
  const location = useLocation();

  if (status === "loading") return <BootScreen />;
  if (status === "unauthenticated") {
    return (
      <Navigate to="/login" replace state={{ from: `${location.pathname}${location.search}` }} />
    );
  }
  return <>{children}</>;
}
