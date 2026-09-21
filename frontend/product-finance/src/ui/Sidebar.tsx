import { useAuth } from "@opsgrids/client";
import {
  BanknoteIcon,
  CreditCardIcon,
  InboxIcon,
  LayoutIcon,
  ReceiptIcon,
  ScaleIcon,
  SettingsIcon,
  ShieldCheckIcon,
  Sidebar as SidebarShell,
  TruckIcon,
  UsersIcon,
} from "@opsgrids/ui";
import type { NavEntry, NavGroup } from "@opsgrids/ui";
import { LogoMark } from "./LogoMark";

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
 * This product's navigation. The shell — account switcher, ⌘K search, active pill, workspace card
 * and theme toggle — is `@opsgrids/ui`'s `Sidebar`. Everything below is the part that is genuinely
 * this product's: its groups, its mark and its wording.
 *
 * "Integrations" and "Help & Getting Started" were removed: both were `ComingSoon` pages with no
 * backend behind them and nothing else in the app links to them — a dead nav entry is worse than
 * no entry (`frontend/CLAUDE.md`, "no dead controls"). The invoices badge that hardcoded `"6"` is
 * gone for the same reason: it never reflected the tenant's real overdue count.
 */
export function Sidebar({ onNavigate }: { onNavigate?: () => void }) {
  const { user, logout } = useAuth();
  const name = user?.full_name ?? "Account";
  const org =
    user?.organizations.find((o) => o.organization_id === user.organization_id) ??
    user?.organizations[0] ??
    null;

  return (
    <SidebarShell
      groups={GROUPS}
      pinned={PINNED}
      account={{
        name,
        email: user?.email ?? "",
        ...(org ? { organizationName: org.name } : {}),
      }}
      onSignOut={() => void logout()}
      logo={<LogoMark className="size-8 shrink-0" />}
      product={{ name: "OpsGrids Finance", tagline: "Billing workspace" }}
      search={{
        label: "Search invoices",
        placeholder: "Search invoices",
        to: "/invoices",
      }}
      {...(onNavigate ? { onNavigate } : {})}
    />
  );
}
