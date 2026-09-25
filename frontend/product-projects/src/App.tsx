import { AppShell as Shell, Button, InlineAlert, moduleIcon } from "@opsgrids/ui";
import { getAuthOrigin, useAdminLinks, useAuth, useCurrentUser, usePermissions, usePlatformModules } from "@opsgrids/client";
import { useNavGroups } from "./ui";
import { LogoMark } from "./ui/LogoMark";

const DASHBOARD_ORIGIN_FALLBACK = "https://app.opsgrids.com";

/**
 * The permission gate fails closed: while `GET /members/me/grants` is loading every write
 * control renders nothing, and that is right. But if the fetch itself fails, every write control
 * stays hidden forever with no explanation — a screen with no buttons and no reason why. This
 * says so once, above every screen, rather than leaving each one to notice on its own.
 */
function PermissionsStatusBanner() {
  const { status, reloadGrants } = usePermissions();
  if (status !== "error") return null;
  return (
    <InlineAlert
      tone="warning"
      title="Couldn't check your permissions"
      action={
        <Button variant="outline" size="sm" onClick={reloadGrants}>
          Try again
        </Button>
      }
    >
      Some actions may be hidden until this is retried. Anything you could already see stays
      visible.
    </InlineAlert>
  );
}

/**
 * The app shell — the sidebar, the rail, the mobile header/bottom nav and the routed main column —
 * is `@opsgrids/ui`'s `AppShell`. This file supplies everything that is this product's: its mark,
 * its own nav, and the cross-product modules and admin links from `@opsgrids/client`'s hooks.
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
      product={{ name: "OpsGrids Projects", mark: <LogoMark className="size-7" /> }}
      nav={groups}
      pinned={pinned}
      modules={modules.map((m) => ({ key: m.key, label: m.label, href: m.href, icon: moduleIcon(m.iconKey) }))}
      adminLinks={adminLinks.map((l) => ({ key: l.key, label: l.label, href: `${adminOrigin}${l.href}` }))}
      user={{ name: currentUser.name, email: currentUser.email, roleLabel: currentUser.roleDisplay }}
      onSignOut={() => void logout()}
      search={{ placeholder: "Search projects", fallback: { label: "Search projects for", to: "/portfolio" } }}
      banner={<PermissionsStatusBanner />}
    />
  );
}
