import { AppShell as Shell } from "@opsgrids/ui";

import { LogoMark } from "./ui/LogoMark";
import { Sidebar } from "./ui/Sidebar";

/**
 * The console shell — the sidebar rail, the mobile drawer and the routed main column — is
 * `@opsgrids/ui`'s `AppShell`. This file supplies the two things that are this app's: its mark and
 * its name in the compact top bar, so staff never mistake it for a customer product.
 */
export function AppShell() {
  return (
    <Shell
      logo={<LogoMark className="size-7" />}
      productName="OpsGrids Internal"
      renderSidebar={({ onNavigate }) => <Sidebar {...(onNavigate ? { onNavigate } : {})} />}
    />
  );
}
