import { AppShell as Shell, Button, InlineAlert } from "@opsgrids/ui";
import { usePermissions } from "@opsgrids/client";
import { Sidebar } from "./ui";
import { LogoMark } from "./ui/LogoMark";

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
    <div className="mb-3">
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
    </div>
  );
}

/**
 * The app shell — the sidebar rail, the mobile drawer and the routed main column — is
 * `@opsgrids/ui`'s `AppShell`. This file supplies the two things that are this product's: its mark
 * and its name in the compact top bar, plus the one place a failed permissions fetch is announced
 * for every screen underneath it.
 */
export function AppShell() {
  return (
    <div className="flex flex-col">
      <div className="mx-auto w-full max-w-[1600px] px-3 pt-3 sm:px-4">
        <PermissionsStatusBanner />
      </div>
      <Shell
        logo={<LogoMark className="size-7" />}
        productName="PulseBoard"
        renderSidebar={({ onNavigate }) => (
          <Sidebar {...(onNavigate ? { onNavigate } : {})} />
        )}
      />
    </div>
  );
}
