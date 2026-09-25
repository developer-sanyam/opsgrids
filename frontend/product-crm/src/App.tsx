import { AppShell as Shell, Button, InlineAlert, moduleIcon } from "@opsgrids/ui";
import { getAuthOrigin, useAdminLinks, useAuth, useCurrentUser, usePermissions, usePlatformModules } from "@opsgrids/client";
import { DirectoryProvider } from "./data/DirectoryProvider";
import { useDirectory } from "./data/directoryContext";
import { useNavGroups } from "./ui";
import { LogoMark } from "./ui/LogoMark";

/** `app.opsgrids.com`'s own routes — see `getAuthOrigin`'s docstring: `null` (dev, same-origin)
 *  falls back to the production dashboard, exactly the literal every admin link used before. */
const DASHBOARD_ORIGIN_FALLBACK = "https://app.opsgrids.com";

/**
 * Once, at the shell — never per screen. If the grants fetch itself failed, `<Can>` and
 * `useCan` fail closed and every write control on every screen simply vanishes with no
 * explanation. This is the explanation: a banner that says permissions couldn't be checked, with
 * a retry that asks again.
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
      Create, edit and delete controls are hidden until this succeeds. Reloading the page also
      retries it.
    </InlineAlert>
  );
}

/**
 * The shared owner/company/contact directory degrading to "Unassigned" / "—" everywhere is
 * correct behaviour for a transient failure the screen doesn't need to interrupt for — but silent
 * forever is not: this says so, once, with a retry.
 */
function DirectoryStatusBanner() {
  const { status, reload } = useDirectory();
  if (status !== "error") return null;
  return (
    <InlineAlert
      tone="warning"
      title="Couldn't load the owner, company and contact directory — names show as “Unassigned” or “—”"
      action={
        <Button size="sm" variant="outline" onClick={reload}>
          Try again
        </Button>
      }
    />
  );
}

/**
 * The app shell — the sidebar, the rail, the mobile header/bottom nav and the routed main column —
 * is `@opsgrids/ui`'s `AppShell`. This file supplies everything that is this product's: its mark,
 * its own nav, the cross-product modules and admin links from `@opsgrids/client`'s hooks, and the
 * shared owner/company/contact directory every routed screen resolves ids against.
 */
export function AppShell() {
  const { logout } = useAuth();
  const currentUser = useCurrentUser();
  const { groups, pinned } = useNavGroups();
  const modules = usePlatformModules();
  const adminLinks = useAdminLinks();
  const adminOrigin = getAuthOrigin() ?? DASHBOARD_ORIGIN_FALLBACK;

  // `RequireAuth` guarantees a session before this mounts; `useCurrentUser` is briefly `null`
  // between that and `/auth/me` resolving.
  if (!currentUser) return null;

  return (
    <DirectoryProvider>
      <Shell
        product={{ name: "OpsGrids Revenue", mark: <LogoMark className="size-7" /> }}
        nav={groups}
        pinned={pinned}
        modules={modules.map((m) => ({ key: m.key, label: m.label, href: m.href, icon: moduleIcon(m.iconKey) }))}
        adminLinks={adminLinks.map((l) => ({ key: l.key, label: l.label, href: `${adminOrigin}${l.href}` }))}
        user={{ name: currentUser.name, email: currentUser.email, roleLabel: currentUser.roleDisplay }}
        onSignOut={() => void logout()}
        search={{ placeholder: "Search deals", fallback: { label: "Search deals for", to: "/deals" } }}
        banner={
          <>
            <GrantsStatusBanner />
            <DirectoryStatusBanner />
          </>
        }
      />
    </DirectoryProvider>
  );
}
