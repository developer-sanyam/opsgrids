import { useProductShell } from "@opsgrids/client";
import { ProductShell } from "@opsgrids/ui";
import { useNavGroups } from "./ui";
import { LogoMark } from "./ui/LogoMark";

const BANNER_COPY = {
  title: "Permissions couldn't be checked — some actions may be hidden",
  body:
    "Create, edit and delete controls are hidden until this succeeds. Reloading the page also retries it.",
};

/**
 * The app shell — the sidebar, the rail, the mobile header/bottom nav and the routed main column —
 * is `@opsgrids/ui`'s `ProductShell`, fed by `useProductShell` from `@opsgrids/client`. This file
 * supplies only what is this product's: its mark, its own nav, its search and its words.
 * The live storefront (`/live/*`) and the theme editor render outside this shell entirely — a
 * buyer's page has no console chrome to inherit.
 */
export function AppShell() {
  const shell = useProductShell();
  const { groups, pinned } = useNavGroups();

  return (
    <ProductShell
      {...shell}
      product={{ name: "OpsGrids Storefront", mark: <LogoMark className="size-7" /> }}
      nav={groups}
      pinned={pinned}
      searchPlaceholder="Search products"
      searchFallback={{ label: "Search products for", to: "/products" }}
      bannerCopy={BANNER_COPY}
      bottomNavExtra={["/products", "/delivery"]}
    />
  );
}
