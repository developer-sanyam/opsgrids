import { AppShell as Shell } from "@atmora/ui";
import { Sidebar } from "./ui";
import { LogoMark } from "./ui/LogoMark";

/**
 * The app shell — the sidebar rail, the mobile drawer and the routed main column — is
 * `@atmora/ui`'s `AppShell`. This file supplies the two things that are this product's: its mark
 * and its name in the compact top bar.
 */
export function AppShell() {
  return (
    <Shell
      logo={<LogoMark className="size-7" />}
      productName="Atmora Revenue"
      renderSidebar={({ onNavigate }) => (
        <Sidebar {...(onNavigate ? { onNavigate } : {})} />
      )}
    />
  );
}
