import { useAuth } from "@atmora/client";
import {
  ClipboardListIcon,
  FactoryIcon,
  FileTextIcon,
  HomeIcon,
  LayersIcon,
  LifeBuoyIcon,
  PlugIcon,
  SettingsIcon,
  Sidebar as SidebarShell,
  TruckIcon,
  UserPlusIcon,
  WarehouseIcon,
} from "@atmora/ui";
import type { NavEntry, NavGroup } from "@atmora/ui";
import { LogoMark } from "./LogoMark";

const GROUPS: NavGroup[] = [
  {
    title: "Main Menu",
    items: [
      { to: "/", label: "Overview", icon: HomeIcon, end: true },
      { to: "/purchase-orders", label: "Purchase Orders", icon: ClipboardListIcon },
      { to: "/inventory", label: "Inventory", icon: LayersIcon },
      { to: "/suppliers", label: "Suppliers", icon: FactoryIcon },
    ],
  },
  {
    title: "Operations",
    items: [
      { to: "/receiving", label: "Receiving", icon: TruckIcon },
      { to: "/warehouses", label: "Warehouses", icon: WarehouseIcon },
      { to: "/reports", label: "Reports", icon: FileTextIcon },
    ],
  },
  {
    title: "Workspace",
    items: [
      { to: "/integrations", label: "Integrations", icon: PlugIcon },
      { to: "/team", label: "Team Members", icon: UserPlusIcon },
    ],
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
      product={{ name: "Atmora Operations", tagline: "Procurement workspace" }}
      search={{
        label: "Search purchase orders",
        placeholder: "Search",
        to: "/purchase-orders",
      }}
      {...(onNavigate ? { onNavigate } : {})}
    />
  );
}
