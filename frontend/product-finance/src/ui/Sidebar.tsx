import { useAuth } from "@atmora/client";
import {
  CreditCardIcon,
  LayoutIcon,
  LifeBuoyIcon,
  PlugIcon,
  ReceiptIcon,
  ScaleIcon,
  SettingsIcon,
  Sidebar as SidebarShell,
  UsersIcon,
} from "@atmora/ui";
import type { NavEntry, NavGroup } from "@atmora/ui";
import { LogoMark } from "./LogoMark";

const GROUPS: NavGroup[] = [
  {
    title: "Main Menu",
    items: [
      { to: "/", label: "Overview", icon: LayoutIcon, end: true },
      { to: "/invoices", label: "Invoices", icon: ReceiptIcon, badge: "6" },
      { to: "/payments", label: "Payments", icon: CreditCardIcon },
    ],
  },
  {
    title: "Books",
    items: [
      { to: "/ledger", label: "Ledger & Reports", icon: ScaleIcon },
      { to: "/customers", label: "Customers", icon: UsersIcon },
    ],
  },
  {
    title: "Workspace",
    items: [{ to: "/integrations", label: "Integrations", icon: PlugIcon }],
  },
];

const PINNED: NavEntry[] = [
  { to: "/settings", label: "Settings", icon: SettingsIcon },
  { to: "/help", label: "Help & Getting Started", icon: LifeBuoyIcon },
];

/**
 * This product's navigation. The shell — account switcher, ⌘K search, active pill, workspace card
 * and theme toggle — is `@atmora/ui`'s `Sidebar`. Everything below is the part that is genuinely
 * this product's: its groups, its mark and its wording.
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
      product={{ name: "Atmora Finance", tagline: "Billing workspace" }}
      search={{
        label: "Search invoices",
        placeholder: "Search invoices",
        to: "/invoices",
      }}
      badgeTone="danger"
      {...(onNavigate ? { onNavigate } : {})}
    />
  );
}
