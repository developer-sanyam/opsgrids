import { useMemo } from "react";

import { useCan, usePermissionStatus } from "@opsgrids/client";
import {
  AlertTriangleIcon,
  BoxIcon,
  CheckSquareIcon,
  ColumnsIcon,
  ClipboardCheckIcon,
  ClipboardListIcon,
  CreditCardIcon,
  DownloadIcon,
  FactoryIcon,
  FileTextIcon,
  FolderIcon,
  GaugeIcon,
  HomeIcon,
  LayersIcon,
  NetworkIcon,
  PackageIcon,
  PlugIcon,
  ReceiptIcon,
  ScaleIcon,
  SendIcon,
  TagIcon,
  ShieldCheckIcon,
  TruckIcon,
  UsersIcon,
  WarehouseIcon,
} from "@opsgrids/ui";
import type { NavEntry, NavGroup } from "@opsgrids/ui";

// Every entry here has a real screen behind it — a nav item whose screen has no backend does not
// belong in the primary nav (frontend/CLAUDE.md: zero placeholder or dead code). Receiving a
// delivery starts from the purchase order ("Receive goods") and its postings are listed under Goods
// Receipts; Reports,
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
      { to: "/purchase-requisitions", label: "Requisitions", icon: FileTextIcon },
      { to: "/purchase-orders", label: "Purchase Orders", icon: ClipboardListIcon },
      { to: "/goods-receipts", label: "Goods Receipts", icon: ReceiptIcon },
      { to: "/inventory", label: "Inventory", icon: LayersIcon },
      { to: "/inventory/categories", label: "Item Categories", icon: FolderIcon },
      { to: "/inventory/scan", label: "Scan", icon: TagIcon },
      { to: "/inventory/import", label: "Import & Export", icon: DownloadIcon },
      { to: "/counting", label: "Cycle Counts", icon: CheckSquareIcon },
      { to: "/quality/inspections", label: "Inspections", icon: ShieldCheckIcon },
      { to: "/quality/templates", label: "QC Templates", icon: ClipboardListIcon },
      { to: "/suppliers", label: "Suppliers", icon: FactoryIcon },
      { to: "/warehouses", label: "Warehouses", icon: WarehouseIcon },
      // The organization's own Razorpay account and payment reconciliation (ADR-0024 A4).
      { to: "/settings/payments", label: "Online Payments", icon: CreditCardIcon },
    ],
  },
  {
    title: "Manufacturing",
    items: [
      { to: "/manufacturing/boms", label: "Bills of materials", icon: NetworkIcon },
      { to: "/manufacturing/orders", label: "Production orders", icon: BoxIcon },
    ],
  },
];

// Stock movements (inventory Phase 3). Each is a screen behind a permission; an entry the person
// cannot use is left out rather than shown and refused. Hiding it is presentation — the screens
// and the endpoints behind them check on their own.
const MOVEMENT_ENTRIES: NavEntry[] = [
  { to: "/inventory/transfers", label: "Transfers", icon: SendIcon },
  { to: "/inventory/reservations", label: "Reservations", icon: TagIcon },
  { to: "/inventory/adjustments", label: "Adjustments", icon: ScaleIcon },
];
const APPROVALS_ENTRY: NavEntry = {
  to: "/inventory/settings/approvals",
  label: "Approval Thresholds",
  icon: GaugeIcon,
};

/**
 * This product's navigation, for `AppShell`'s `nav` prop.
 *
 * Under Inventory: Stock, Lots, Rolls and the Stock Ledger (Phase 1), then Transfers, Reservations
 * and Adjustments (Phase 3), all for a caller who can read stock, and Approval Thresholds for one
 * who can configure them. A Manufacturing entry is hidden from a member **known** not to hold its
 * read permission, and shown while that is not yet known, so the rail does not flash entries in
 * and out as grants arrive. Hiding a link is presentation; every endpoint behind them refuses the
 * call on its own.
 */
export function useNavGroups(): { groups: NavGroup[]; pinned: NavEntry[] } {
  const canReadStock = useCan("operations.inventory.stock.read", "ORG");
  const canConfigure = useCan("operations.inventory.control.configure", "ORG");
  const bomRead = usePermissionStatus("operations.manufacturing.bom.read", "ORG");
  const orderRead = usePermissionStatus("operations.manufacturing.production_order.read", "ORG");

  const groups = useMemo(() => {
    const hidden = new Set([
      ...(bomRead === "denied" ? ["/manufacturing/boms"] : []),
      ...(orderRead === "denied" ? ["/manufacturing/orders"] : []),
    ]);
    return GROUPS.map((group) => ({
      ...group,
      items: group.items
        .flatMap((entry) =>
          entry.to === "/inventory"
            ? [
                entry,
                ...(canReadStock ? [...STOCK_ENTRIES, ...MOVEMENT_ENTRIES] : []),
                ...(canConfigure ? [APPROVALS_ENTRY] : []),
              ]
            : [entry],
        )
        .filter((item) => !hidden.has(item.to)),
    })).filter((group) => group.items.length > 0);
  }, [canReadStock, canConfigure, bomRead, orderRead]);
  return { groups, pinned: [] };
}
