import { useCan } from "@opsgrids/client";
import {
  AlertTriangleIcon,
  BoxIcon,
  CheckSquareIcon,
  ColumnsIcon,
  ClipboardCheckIcon,
  ClipboardListIcon,
  CreditCardIcon,
  FactoryIcon,
  FolderIcon,
  HomeIcon,
  LayersIcon,
  PackageIcon,
  PlugIcon,
  ReceiptIcon,
  TagIcon,
  TruckIcon,
  UsersIcon,
  WarehouseIcon,
} from "@opsgrids/ui";
import type { NavEntry, NavGroup } from "@opsgrids/ui";

// Every entry here has a real screen behind it — a nav item whose screen has no backend does not
// belong in the primary nav (frontend/CLAUDE.md: zero placeholder or dead code). "Receiving" was
// folded into the purchase order detail page's own "Mark as received" action; Reports,
// Integrations, Team and Settings have no backend yet and were removed rather than left as
// ComingSoon placeholders.
const STOCK_ENTRIES: NavEntry[] = [
  { to: "/inventory/stock", label: "Stock", icon: BoxIcon },
  { to: "/inventory/lots", label: "Lots", icon: TagIcon },
  { to: "/inventory/rolls", label: "Rolls", icon: ColumnsIcon },
  { to: "/inventory/ledger", label: "Stock Ledger", icon: ReceiptIcon },
];

const GROUPS: NavGroup[] = [
  {
    title: "Main Menu",
    items: [
      { to: "/", label: "Overview", icon: HomeIcon, end: true },
      { to: "/orders", label: "Orders", icon: PackageIcon },
      { to: "/fulfilment/tasks", label: "Tasks", icon: ClipboardCheckIcon },
      { to: "/fulfilment/workers", label: "Workers", icon: UsersIcon },
      { to: "/dispatch", label: "Dispatch", icon: TruckIcon },
      { to: "/automation", label: "Automation", icon: AlertTriangleIcon },
      { to: "/channels", label: "Channels", icon: PlugIcon },
      { to: "/purchase-orders", label: "Purchase Orders", icon: ClipboardListIcon },
      { to: "/inventory", label: "Inventory", icon: LayersIcon },
      { to: "/inventory/categories", label: "Item Categories", icon: FolderIcon },
      { to: "/counting", label: "Cycle Counts", icon: CheckSquareIcon },
      { to: "/suppliers", label: "Suppliers", icon: FactoryIcon },
      { to: "/warehouses", label: "Warehouses", icon: WarehouseIcon },
      // The organization's own Razorpay account and payment reconciliation (ADR-0024 A4).
      { to: "/settings/payments", label: "Online Payments", icon: CreditCardIcon },
    ],
  },
];

/**
 * This product's navigation, for `AppShell`'s `nav` prop.
 *
 * Stock, Lots, Rolls and the Stock Ledger sit under Inventory and appear only for a caller who
 * can read stock — hiding the link is presentation; every endpoint behind them refuses the call
 * on its own.
 */
export function useNavGroups(): { groups: NavGroup[]; pinned: NavEntry[] } {
  const canReadStock = useCan("operations.inventory.stock.read", "ORG");
  if (!canReadStock) return { groups: GROUPS, pinned: [] };
  const groups = GROUPS.map((group) => {
    const at = group.items.findIndex((entry) => entry.to === "/inventory");
    if (at === -1) return group;
    return { ...group, items: [...group.items.slice(0, at + 1), ...STOCK_ENTRIES, ...group.items.slice(at + 1)] };
  });
  return { groups, pinned: [] };
}
