import {
  CheckSquareIcon,
  ClipboardCheckIcon,
  ClipboardListIcon,
  FactoryIcon,
  FolderIcon,
  HomeIcon,
  LayersIcon,
  PackageIcon,
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
const GROUPS: NavGroup[] = [
  {
    title: "Main Menu",
    items: [
      { to: "/", label: "Overview", icon: HomeIcon, end: true },
      { to: "/orders", label: "Orders", icon: PackageIcon },
      { to: "/fulfilment/tasks", label: "Tasks", icon: ClipboardCheckIcon },
      { to: "/fulfilment/workers", label: "Workers", icon: UsersIcon },
      { to: "/dispatch", label: "Dispatch", icon: TruckIcon },
      { to: "/purchase-orders", label: "Purchase Orders", icon: ClipboardListIcon },
      { to: "/inventory", label: "Inventory", icon: LayersIcon },
      { to: "/inventory/categories", label: "Item Categories", icon: FolderIcon },
      { to: "/counting", label: "Cycle Counts", icon: CheckSquareIcon },
      { to: "/suppliers", label: "Suppliers", icon: FactoryIcon },
      { to: "/warehouses", label: "Warehouses", icon: WarehouseIcon },
    ],
  },
];

/** This product's navigation, for `AppShell`'s `nav` prop. */
export function useNavGroups(): { groups: NavGroup[]; pinned: NavEntry[] } {
  return { groups: GROUPS, pinned: [] };
}
