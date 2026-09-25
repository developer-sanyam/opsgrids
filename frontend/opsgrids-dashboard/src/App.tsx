import { Navigate, useLocation } from "react-router-dom";

import { useAuth, useCurrentUser, usePermissions, usePlatformModules } from "@opsgrids/client";
import { AppShell as Shell, InlineAlert, moduleIcon } from "@opsgrids/ui";

import { LogoMark, useNavGroups } from "./ui";

/**
 * The app shell is `@opsgrids/ui`'s; this file supplies the mark, the nav, the cross-product
 * modules from `@opsgrids/client`'s hooks, the one place a failed permissions fetch is surfaced,
 * and the `must_change_password` redirect every screen has to honour once, rather than each screen
 * checking it for itself.
 *
 * `<Can>` and `<RequirePermission>` fail closed — every write control across the app simply
 * vanishes on a grants error — so the permissions banner turns that silence into an explanation,
 * shown once for the whole app rather than once per screen.
 *
 * No `adminLinks`: this app *is* the admin console, and its own richer "Administration" group
 * (`useNavGroups`) already covers what `useAdminLinks` would only repeat a narrower version of.
 */
export function AppShell() {
  const { status, error, reloadGrants } = usePermissions();
  const { user, logout } = useAuth();
  const currentUser = useCurrentUser();
  const location = useLocation();
  const { groups, pinned } = useNavGroups();
  const modules = usePlatformModules();

  if (user?.must_change_password === true && location.pathname !== "/account/security") {
    return <Navigate to="/account/security" replace state={{ from: location.pathname }} />;
  }

  if (!currentUser) return null;

  return (
    <Shell
      product={{ name: "OpsGrids", mark: <LogoMark className="size-7" /> }}
      nav={groups}
      pinned={pinned}
      modules={modules.map((m) => ({ key: m.key, label: m.label, href: m.href, icon: moduleIcon(m.iconKey) }))}
      user={{ name: currentUser.name, email: currentUser.email, roleLabel: currentUser.roleDisplay }}
      onSignOut={() => void logout()}
      search={{ placeholder: "Search products", fallback: { label: "Search products for", to: "/" } }}
      helpHref="/help"
      banner={
        <>
          {user?.must_change_password === true ? (
            <InlineAlert tone="warning" title="Change your password to continue." live="off" />
          ) : null}
          {status === "error" ? (
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
          ) : null}
        </>
      }
    />
  );
}
