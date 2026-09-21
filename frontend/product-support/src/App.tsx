import { usePermissions } from "@opsgrids/client";
import { AppShell as Shell, Button, InlineAlert } from "@opsgrids/ui";
import { Sidebar } from "./ui";
import { LogoMark } from "./ui/LogoMark";

/**
 * The app shell — the sidebar rail, the mobile drawer and the routed main column — is
 * `@opsgrids/ui`'s `AppShell`. This file supplies the two things that are this product's: its mark
 * and its name in the compact top bar, plus the one permission-health banner every screen in the
 * app shares. `<Can>` and `useCan` fail closed on their own (a control just does not render), but
 * a screen with every write control missing and no explanation is indistinguishable from a narrow
 * role — this says so, once, rather than on every screen that happens to have a write control.
 */
export function AppShell() {
  const { status, reloadGrants } = usePermissions();

  return (
    <>
      {status === "error" ? (
        <div className="mx-auto max-w-[1600px] px-3 pt-3 sm:px-4">
          <InlineAlert
            tone="warning"
            title="We couldn't check your permissions. Some actions may be hidden until this is fixed."
            action={
              <Button variant="outline" size="sm" onClick={reloadGrants}>
                Try again
              </Button>
            }
          />
        </div>
      ) : null}
      <Shell
        logo={<LogoMark className="size-7" />}
        productName="OpsGrids Support"
        renderSidebar={({ onNavigate }) => (
          <Sidebar {...(onNavigate ? { onNavigate } : {})} />
        )}
      />
    </>
  );
}
