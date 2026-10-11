import { useMemo } from "react";
import { useLocation } from "react-router-dom";

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
  MapPinIcon,
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
      // Material sent to an outside processor and expected back (Inventory plan Phase 6).
      { to: "/jobwork/orders", label: "Job Work", icon: ReceiptIcon },
      { to: "/quality/inspections", label: "Inspections", icon: ShieldCheckIcon },
      { to: "/quality/templates", label: "QC Templates", icon: ClipboardListIcon },
      { to: "/suppliers", label: "Suppliers", icon: FactoryIcon },
      { to: "/warehouses", label: "Warehouses", icon: WarehouseIcon },
      { to: "/warehouses/map", label: "Warehouse Map", icon: MapPinIcon },
      // The organization's own payment-provider account (Razorpay or Cashfree) and payment
      // reconciliation (ADR-0024 A4, ADR-0033).
      { to: "/settings/payments", label: "Online Payments", icon: CreditCardIcon },
    ],
  },
  {
    title: "Manufacturing",
    items: [
      { to: "/manufacturing/boms", label: "Bills of materials", icon: NetworkIcon },
      { to: "/manufacturing/orders", label: "Production orders", icon: BoxIcon },
      { to: "/manufacturing/mrp", label: "Material planning", icon: ClipboardListIcon },
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
  const { pathname } = useLocation();
  const canConfigure = useCan("operations.inventory.control.configure", "ORG");
  const bomRead = usePermissionStatus("operations.manufacturing.bom.read", "ORG");
  const orderRead = usePermissionStatus("operations.manufacturing.production_order.read", "ORG");

  const mrpRead = usePermissionStatus("operations.manufacturing.mrp.read", "ORG");

  const groups = useMemo(() => {
    const hidden = new Set([
      ...(bomRead === "denied" ? ["/manufacturing/boms"] : []),
      ...(orderRead === "denied" ? ["/manufacturing/orders"] : []),
      ...(mrpRead === "denied" ? ["/manufacturing/mrp"] : []),
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
            : // "Warehouses" is active for every screen under /warehouses (locations, floors,
              // 3D) — except the map, which has an entry of its own: there it matches the exact
              // path only, so one screen never lights two entries.
              entry.to === "/warehouses"
              ? [{ ...entry, end: pathname === "/warehouses/map" }]
              : [entry],
        )
        .filter((item) => !hidden.has(item.to)),
    })).filter((group) => group.items.length > 0);
  }, [canReadStock, canConfigure, bomRead, orderRead, mrpRead, pathname]);
  return { groups, pinned: [] };
}
