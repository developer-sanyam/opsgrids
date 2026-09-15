import { useAuth } from "@opsgrids/client";
import {
  BuildingUserIcon,
  FileTextIcon,
  GaugeIcon,
  HomeIcon,
  InboxIcon,
  LifeBuoyIcon,
  PlugIcon,
  SettingsIcon,
  Sidebar as SidebarShell,
  TagIcon,
  UserPlusIcon,
} from "@opsgrids/ui";
import type { NavEntry, NavGroup } from "@opsgrids/ui";
import { LogoMark } from "./LogoMark";

const GROUPS: NavGroup[] = [
  {
    title: "Support",
    items: [
      { to: "/", label: "Overview", icon: HomeIcon, end: true },
      { to: "/queue", label: "Queue", icon: InboxIcon, badge: "14" },
      { to: "/agents", label: "SLA & Agents", icon: GaugeIcon },
    ],
  },
  {
    title: "Manage",
    items: [
      { to: "/customers", label: "Customers", icon: BuildingUserIcon },
      { to: "/topics", label: "Topics & Tags", icon: TagIcon },
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
      product={{ name: "OpsGrids Support", tagline: "Support workspace" }}
      search={{
        label: "Search tickets",
        placeholder: "Search tickets",
        to: "/queue",
      }}
      {...(onNavigate ? { onNavigate } : {})}
    />
  );
}
