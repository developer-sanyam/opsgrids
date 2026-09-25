import { AppShell as Shell, Button, InlineAlert, moduleIcon } from "@opsgrids/ui";
import { getAuthOrigin, useAdminLinks, useAuth, useCurrentUser, usePermissions, usePlatformModules } from "@opsgrids/client";
import { useNavGroups } from "./ui";
import { LogoMark } from "./ui/LogoMark";

const DASHBOARD_ORIGIN_FALLBACK = "https://app.opsgrids.com";

/**
 * Once, at the shell — never per screen. If the grants fetch itself failed, `Can` and `useCan`
 * fail closed and every write control on every screen simply vanishes with no explanation. This
 * is the explanation: a banner that says permissions couldn't be checked, with a retry that asks
 * again.
 */
function GrantsStatusBanner() {
  const { status, reloadGrants } = usePermissions();
  if (status !== "error") return null;
  return (
    <InlineAlert
      tone="warning"
      title="Permissions couldn't be checked — some actions may be hidden"
      action={
        <Button size="sm" variant="outline" onClick={reloadGrants}>
          Try again
        </Button>
      }
    >
      Create, edit, delete and status controls are hidden until this succeeds. Reloading the page
      also retries it.
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
      product={{ name: "OpsGrids Marketing", mark: <LogoMark className="size-7" /> }}
      nav={groups}
      pinned={pinned}
      modules={modules.map((m) => ({ key: m.key, label: m.label, href: m.href, icon: moduleIcon(m.iconKey) }))}
      adminLinks={adminLinks.map((l) => ({ key: l.key, label: l.label, href: `${adminOrigin}${l.href}` }))}
      user={{ name: currentUser.name, email: currentUser.email, roleLabel: currentUser.roleDisplay }}
      onSignOut={() => void logout()}
      search={{ placeholder: "Search campaigns", fallback: { label: "Search campaigns for", to: "/campaigns" } }}
      banner={<GrantsStatusBanner />}
    />
  );
}
