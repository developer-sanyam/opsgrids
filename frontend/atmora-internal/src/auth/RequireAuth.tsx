import type { ReactNode } from "react";
import { Navigate, useLocation } from "react-router-dom";

import { LogoMark } from "@/ui/icons";
import { useAuth } from "./context";

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
 * there is no session (remembering where the operator was headed), and otherwise renders the app.
 */
export function RequireAuth({ children }: { children: ReactNode }) {
  const { status } = useAuth();
  const location = useLocation();

  if (status === "loading") return <BootScreen />;
  if (status === "unauthenticated") {
    return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  }
  return <>{children}</>;
}
