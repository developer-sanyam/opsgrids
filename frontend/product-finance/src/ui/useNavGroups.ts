import {
  BanknoteIcon,
  CreditCardIcon,
  InboxIcon,
  LayoutIcon,
  ReceiptIcon,
  ScaleIcon,
  SettingsIcon,
  ShieldCheckIcon,
  TruckIcon,
  UsersIcon,
} from "@opsgrids/ui";
import type { NavEntry, NavGroup } from "@opsgrids/ui";

const GROUPS: NavGroup[] = [
  {
    title: "Main Menu",
    items: [
      { to: "/", label: "Overview", icon: LayoutIcon, end: true },
      { to: "/invoices", label: "Invoices", icon: ReceiptIcon },
      { to: "/payments", label: "Payments", icon: CreditCardIcon },
    ],
  },
  {
    title: "Books",
    items: [
      { to: "/ledger", label: "Ledger & Reports", icon: ScaleIcon },
      { to: "/customers", label: "Customers", icon: UsersIcon },
      { to: "/purchases", label: "Purchases", icon: BanknoteIcon },
    ],
  },
  {
    title: "Compliance",
    items: [
      { to: "/compliance/ims", label: "Supplier reconciliation", icon: InboxIcon },
      { to: "/compliance/returns", label: "GST returns", icon: ShieldCheckIcon },
      { to: "/compliance/eway", label: "e-Way bills", icon: TruckIcon },
      { to: "/compliance/withholding", label: "TDS / TCS", icon: ScaleIcon },
    ],
  },
];

const PINNED: NavEntry[] = [{ to: "/settings", label: "Settings", icon: SettingsIcon }];

/**
 * This product's navigation, for `AppShell`'s `nav`/`pinned` props.
 *
 * "Integrations" and "Help & Getting Started" stay gone: both were `ComingSoon` pages with no
 * backend behind them and nothing else in the app links to them — a dead nav entry is worse than
 * no entry (`frontend/CLAUDE.md`, "no dead controls").
 */
export function useNavGroups(): { groups: NavGroup[]; pinned: NavEntry[] } {
  return { groups: GROUPS, pinned: PINNED };
}
