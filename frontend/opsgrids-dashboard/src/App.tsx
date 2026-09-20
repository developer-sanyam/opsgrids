import { AppShell as Shell } from "@opsgrids/ui";

import { LogoMark, Sidebar } from "./ui";

/** The app shell is `@opsgrids/ui`'s; this file supplies the mark and the name. */
export function AppShell() {
  return (
    <Shell
      logo={<LogoMark className="size-7" />}
      productName="OpsGrids"
      renderSidebar={({ onNavigate }) => <Sidebar {...(onNavigate ? { onNavigate } : {})} />}
    />
  );
}
