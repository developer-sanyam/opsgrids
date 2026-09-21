import { useAuth } from "@opsgrids/client";
import {
  ClipboardListIcon,
  FactoryIcon,
  HomeIcon,
  LayersIcon,
  Sidebar as SidebarShell,
  WarehouseIcon,
} from "@opsgrids/ui";
import type { NavGroup } from "@opsgrids/ui";
import { LogoMark } from "./LogoMark";

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
      { to: "/purchase-orders", label: "Purchase Orders", icon: ClipboardListIcon },
      { to: "/inventory", label: "Inventory", icon: LayersIcon },
      { to: "/suppliers", label: "Suppliers", icon: FactoryIcon },
      { to: "/warehouses", label: "Warehouses", icon: WarehouseIcon },
    ],
  },
];

/**
 * This product's navigation. The shell — account switcher, ⌘K search, active pill, workspace card
 * and theme toggle — is `@opsgrids/ui`'s `Sidebar`. Everything below is the part that is genuinely
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
      pinned={[]}
      account={{
        name,
        email: user?.email ?? "",
        ...(org ? { organizationName: org.name } : {}),
      }}
      onSignOut={() => void logout()}
      logo={<LogoMark className="size-8 shrink-0" />}
      product={{ name: "OpsGrids Operations", tagline: "Procurement workspace" }}
      search={{
        label: "Search purchase orders",
        placeholder: "Search",
        to: "/purchase-orders",
      }}
      {...(onNavigate ? { onNavigate } : {})}
    />
  );
}
