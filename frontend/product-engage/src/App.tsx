import { useProductShell } from "@opsgrids/client";
import { ProductShell } from "@opsgrids/ui";
import { USE_FIXTURES } from "./api/client";
import { useNavGroups } from "./ui";
import { LogoMark } from "./ui/LogoMark";

/**
 * The app shell — the sidebar, the rail, the mobile header/bottom nav and the routed main column —
 * is `@opsgrids/ui`'s `ProductShell`, fed by `useProductShell` from `@opsgrids/client`. This file
 * supplies only what is this product's: its mark, its own nav, its search and its words.
 *
 * No grants banner: this is the only app whose screens gate themselves individually
 * (`RequireGrant`) rather than failing an entire shell closed on one fetch. The bell is left out
 * in sample-data mode: the feed is a gateway read with no sample answer.
 */
export function AppShell() {
  const shell = useProductShell({ notifications: !USE_FIXTURES });
  const { groups, pinned } = useNavGroups();

  return (
    <ProductShell
      {...shell}
      product={{ name: "OpsGrids Engage", mark: <LogoMark className="size-7" /> }}
      nav={groups}
      pinned={pinned}
      searchPlaceholder="Search contacts"
      searchFallback={{ label: "Search contacts for", to: "/contacts" }}
      bottomNavExtra={["/inbox", "/campaigns"]}
    />
  );
}
