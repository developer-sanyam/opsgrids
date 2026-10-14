import { useProductShell } from "@opsgrids/client";
import { Button, InlineAlert, ProductShell } from "@opsgrids/ui";
import { DirectoryProvider } from "./data/DirectoryProvider";
import { useDirectory } from "./data/directoryContext";
import { useNavGroups } from "./ui";
import { LogoMark } from "./ui/LogoMark";

const BANNER_COPY = {
  title: "Permissions couldn't be checked — some actions may be hidden",
  body:
    "Create, edit and delete controls are hidden until this succeeds. Reloading the page also retries it.",
};

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
        <Button size="sm" variant="secondary" onClick={reload}>
          Try again
        </Button>
      }
    />
  );
}

/**
 * The app shell — the sidebar, the rail, the mobile header/bottom nav and the routed main column —
 * is `@opsgrids/ui`'s `ProductShell`, fed by `useProductShell` from `@opsgrids/client`. This file
 * supplies only what is this product's: its mark, its own nav, its search and its words.
 * Around it sits the shared owner/company/contact directory every routed screen resolves ids against.
 */
export function AppShell() {
  const shell = useProductShell();
  const { groups, pinned } = useNavGroups();
  if (shell.user === null) return null;

  return (
    <DirectoryProvider>
      <ProductShell
        {...shell}
        product={{ name: "OpsGrids Revenue", mark: <LogoMark className="size-7" /> }}
        nav={groups}
        pinned={pinned}
        searchPlaceholder="Search deals"
        searchFallback={{ label: "Search deals for", to: "/deals" }}
        bannerCopy={BANNER_COPY}
        extraBanners={<DirectoryStatusBanner />}
        bottomNavExtra={["/leads", "/deals", "/contacts"]}
      />
    </DirectoryProvider>
  );
}
