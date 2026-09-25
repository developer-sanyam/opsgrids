import { getAuthOrigin, useAdminLinks, useAuth, useCurrentUser, usePermissions, usePlatformModules } from "@opsgrids/client";
import { AppShell as Shell, Button, InlineAlert, moduleIcon } from "@opsgrids/ui";
import { useNavGroups } from "./ui";
import { LogoMark } from "./ui/LogoMark";

const DASHBOARD_ORIGIN_FALLBACK = "https://app.opsgrids.com";

/**
 * The app shell — the sidebar, the rail, the mobile header/bottom nav and the routed main column —
 * is `@opsgrids/ui`'s `AppShell`. This file supplies everything that is this product's: its mark,
 * its own nav, and the cross-product modules and admin links from `@opsgrids/client`'s hooks, plus
 * the one permission-health banner every screen in the app shares. `<Can>` and `useCan` fail
 * closed on their own (a control just does not render), but a screen with every write control
 * missing and no explanation is indistinguishable from a narrow role — this says so, once, rather
 * than on every screen that happens to have a write control.
 */
export function AppShell() {
  const { status, reloadGrants } = usePermissions();
  const { logout } = useAuth();
  const currentUser = useCurrentUser();
  const { groups, pinned } = useNavGroups();
  const modules = usePlatformModules();
  const adminLinks = useAdminLinks();
  const adminOrigin = getAuthOrigin() ?? DASHBOARD_ORIGIN_FALLBACK;

  if (!currentUser) return null;

  return (
    <Shell
      product={{ name: "OpsGrids Support", mark: <LogoMark className="size-7" /> }}
      nav={groups}
      pinned={pinned}
      modules={modules.map((m) => ({ key: m.key, label: m.label, href: m.href, icon: moduleIcon(m.iconKey) }))}
      adminLinks={adminLinks.map((l) => ({ key: l.key, label: l.label, href: `${adminOrigin}${l.href}` }))}
      user={{ name: currentUser.name, email: currentUser.email, roleLabel: currentUser.roleDisplay }}
      onSignOut={() => void logout()}
      search={{ placeholder: "Search tickets", fallback: { label: "Search tickets for", to: "/queue" } }}
      banner={
        status === "error" ? (
          <InlineAlert
            tone="warning"
            title="We couldn't check your permissions. Some actions may be hidden until this is fixed."
            action={
              <Button variant="outline" size="sm" onClick={reloadGrants}>
                Try again
              </Button>
            }
          />
        ) : null
      }
    />
  );
}
