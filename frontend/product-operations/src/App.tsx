import { useCan, useProductShell } from "@opsgrids/client";
import { ClipboardListIcon, PackageIcon, ProductShell, TagIcon } from "@opsgrids/ui";
import type { ProductQuickAction } from "@opsgrids/ui";
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
 */
export function AppShell() {
  const shell = useProductShell();
  const { groups, pinned } = useNavGroups();
  const canWritePurchaseOrder = useCan("operations.procurement.purchase_order.write", "OWN");
  const canCreateOrder = useCan("operations.orders.order.create");
  const canScan = useCan("operations.inventory.stock.read", "ORG");
  const quickActions: ProductQuickAction[] = [
    ...(canWritePurchaseOrder
      ? [{ key: "new-purchase-order", label: "New purchase order", icon: ClipboardListIcon, to: "/purchase-orders/new" }]
      : []),
    ...(canCreateOrder ? [{ key: "new-order", label: "New order", icon: PackageIcon, to: "/orders/new" }] : []),
    ...(canScan ? [{ key: "scan", label: "Scan", icon: TagIcon, to: "/inventory/scan" }] : []),
  ];

  return (
    <ProductShell
      {...shell}
      product={{ name: "OpsGrids Operations", mark: <LogoMark className="size-7" /> }}
      nav={groups}
      pinned={pinned}
      searchPlaceholder="Search purchase orders"
      searchFallback={{ label: "Search purchase orders for", to: "/purchase-orders" }}
      bannerCopy={BANNER_COPY}
      bottomNavExtra={["/orders", "/inventory", "/inventory/scan"]}
      quickActions={quickActions}
    />
  );
}
