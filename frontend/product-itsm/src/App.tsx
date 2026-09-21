import { AppShell as Shell, Button, InlineAlert } from "@opsgrids/ui";
import { usePermissions } from "@opsgrids/client";
import { Sidebar } from "./ui";
import { LogoMark } from "./ui/LogoMark";

/**
 * Once, at the shell — never per screen. If the grants fetch itself failed, `<Can>` and `useCan`
 * fail closed and every write control on every screen simply vanishes with no explanation. This
 * is the explanation: a banner that says permissions couldn't be checked, with a retry that asks
 * again.
 */
function GrantsStatusBanner() {
  const { status, reloadGrants } = usePermissions();
  if (status !== "error") return null;
  return (
    <div className="sticky top-0 z-40 px-3 pt-3 sm:px-4 sm:pt-4">
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
    </div>
  );
}

/**
 * The app shell — the sidebar rail, the mobile drawer and the routed main column — is
 * `@opsgrids/ui`'s `AppShell`. This file supplies the two things that are this product's: its mark
 * and its name in the compact top bar, plus the permissions-unreachable banner every screen relies
 * on the shell to show once rather than duplicating it.
 */
export function AppShell() {
  return (
    <>
      <GrantsStatusBanner />
      <Shell
        logo={<LogoMark className="size-7" />}
        productName="OpsGrids IT"
        renderSidebar={({ onNavigate }) => (
          <Sidebar {...(onNavigate ? { onNavigate } : {})} />
        )}
      />
    </>
  );
}
