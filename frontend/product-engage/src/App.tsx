import { getAuthOrigin, NotificationsBell, useAdminLinks, useAuth, useCurrentUser, usePlatformModules } from "@opsgrids/client";
import { AppShell as Shell, moduleIcon } from "@opsgrids/ui";
import { useNavGroups } from "./ui";
import { LogoMark } from "./ui/LogoMark";

const DASHBOARD_ORIGIN_FALLBACK = "https://app.opsgrids.com";

/**
 * The app shell — the sidebar, the rail, the mobile header/bottom nav and the routed main column —
 * is `@opsgrids/ui`'s `AppShell`. This file supplies everything that is this product's: its mark,
 * its own nav, and the cross-product modules and admin links from `@opsgrids/client`'s hooks.
 *
 * No grants banner: this is the only app whose screens gate themselves individually
 * (`RequireGrant`) rather than failing an entire shell closed on one fetch.
 */
export function AppShell() {
  const { logout } = useAuth();
  const currentUser = useCurrentUser();
  const { groups, pinned } = useNavGroups();
  const modules = usePlatformModules();
  const adminLinks = useAdminLinks();
  const adminOrigin = getAuthOrigin() ?? DASHBOARD_ORIGIN_FALLBACK;

  if (!currentUser) return null;

  return (
    <Shell
      product={{ name: "OpsGrids Engage", mark: <LogoMark className="size-7" /> }}
      nav={groups}
      pinned={pinned}
      modules={modules.map((m) => ({ key: m.key, label: m.label, href: m.href, icon: moduleIcon(m.iconKey) }))}
      adminLinks={adminLinks.map((l) => ({ key: l.key, label: l.label, href: `${adminOrigin}${l.href}` }))}
      user={{ name: currentUser.name, email: currentUser.email, roleLabel: currentUser.roleDisplay }}
      onSignOut={() => void logout()}
      search={{ placeholder: "Search domains", fallback: { label: "Search domains for", to: "/domains" } }}
      notifications={<NotificationsBell />}
    />
  );
}
