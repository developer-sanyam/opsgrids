import { Navigate, useLocation } from "react-router-dom";

import { useAuth, usePermissions } from "@opsgrids/client";
import { AppShell as Shell, InlineAlert } from "@opsgrids/ui";

import { LogoMark, Sidebar } from "./ui";

/**
 * The app shell is `@opsgrids/ui`'s; this file supplies the mark, the name, the one place a
 * failed permissions fetch is surfaced, and the `must_change_password` redirect every screen
 * has to honour once, rather than each screen checking it for itself.
 *
 * `<Can>` and `<RequirePermission>` fail closed — every write control across the app simply
 * vanishes on a grants error — so the permissions banner turns that silence into an explanation,
 * shown once for the whole app rather than once per screen.
 */
export function AppShell() {
  const { status, error, reloadGrants } = usePermissions();
  const { user } = useAuth();
  const location = useLocation();

  if (user?.must_change_password === true && location.pathname !== "/account/security") {
    return <Navigate to="/account/security" replace state={{ from: location.pathname }} />;
  }

  return (
    <>
      {user?.must_change_password === true ? (
        <div className="mx-auto max-w-[1600px] px-3 pt-3 sm:px-4 sm:pt-4">
          <InlineAlert tone="warning" title="Change your password to continue." live="off" />
        </div>
      ) : null}
      {status === "error" ? (
        <div className="mx-auto max-w-[1600px] px-3 pt-3 sm:px-4 sm:pt-4">
          <InlineAlert
            tone="warning"
            title="We couldn't check your permissions, so some actions are hidden."
            action={
              <button
                type="button"
                onClick={reloadGrants}
                className="text-sm font-semibold text-accent-ink underline-offset-2 hover:underline"
              >
                Try again
              </button>
            }
          >
            {error ? error.message : null}
          </InlineAlert>
        </div>
      ) : null}
      <Shell
        logo={<LogoMark className="size-7" />}
        productName="OpsGrids"
        renderSidebar={({ onNavigate }) => <Sidebar {...(onNavigate ? { onNavigate } : {})} />}
      />
    </>
  );
}
