import { useAuth } from "@opsgrids/client";
import {
  BoxIcon,
  LayoutIcon,
  LifeBuoyIcon,
  MonitorIcon,
  PackageIcon,
  SettingsIcon,
  Sidebar as SidebarShell,
  TagIcon,
} from "@opsgrids/ui";
import type { NavEntry, NavGroup } from "@opsgrids/ui";
import { LogoMark } from "./LogoMark";

const GROUPS: NavGroup[] = [
  {
    title: "Store",
    items: [
      { to: "/", label: "Themes", icon: LayoutIcon, end: true },
      { to: "/editor", label: "Editor", icon: BoxIcon },
    ],
  },
  {
    title: "Preview",
    items: [
      { to: "/store", label: "Storefront", icon: MonitorIcon, end: true },
      { to: "/store/catalogue", label: "Catalogue", icon: PackageIcon },
      { to: "/store/product", label: "Line detail", icon: TagIcon },
    ],
  },
];

const PINNED: NavEntry[] = [
  { to: "/settings", label: "Settings", icon: SettingsIcon },
  { to: "/help", label: "Help & Getting Started", icon: LifeBuoyIcon },
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
      pinned={PINNED}
      account={{
        name,
        email: user?.email ?? "",
        ...(org ? { organizationName: org.name } : {}),
      }}
      onSignOut={() => void logout()}
      logo={<LogoMark className="size-8 shrink-0" />}
      product={{ name: "OpsGrids Storefront", tagline: "Themed B2B store" }}
      search={{
        label: "Search themes",
        placeholder: "Search themes",
        to: "/",
      }}
      {...(onNavigate ? { onNavigate } : {})}
    />
  );
}
