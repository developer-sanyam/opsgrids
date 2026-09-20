import { AppShell as Shell } from "@opsgrids/ui";
import { DirectoryProvider } from "./data/DirectoryProvider";
import { Sidebar } from "./ui";
import { LogoMark } from "./ui/LogoMark";

/**
 * The app shell — the sidebar rail, the mobile drawer and the routed main column — is
 * `@opsgrids/ui`'s `AppShell`. This file supplies the two things that are this product's: its mark
 * and its name in the compact top bar, plus the shared owner/company/contact directory every
 * routed screen resolves ids against.
 */
export function AppShell() {
  return (
    <DirectoryProvider>
      <Shell
        logo={<LogoMark className="size-7" />}
        productName="OpsGrids Revenue"
        renderSidebar={({ onNavigate }) => (
          <Sidebar {...(onNavigate ? { onNavigate } : {})} />
        )}
      />
    </DirectoryProvider>
  );
}
