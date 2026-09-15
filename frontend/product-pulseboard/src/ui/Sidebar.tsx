import { useAuth } from "@opsgrids/client";
import {
  ClockIcon,
  ColumnsIcon,
  FileTextIcon,
  GaugeIcon,
  LayersIcon,
  LifeBuoyIcon,
  MessageSquareIcon,
  PlugIcon,
  SettingsIcon,
  Sidebar as SidebarShell,
  UserPlusIcon,
  UsersIcon,
} from "@opsgrids/ui";
import type { NavEntry, NavGroup } from "@opsgrids/ui";
import { LogoMark } from "./LogoMark";

const GROUPS: NavGroup[] = [
  {
    title: "Main Menu",
    items: [
      { to: "/", label: "Overview", icon: GaugeIcon, end: true },
      { to: "/board", label: "Board", icon: ColumnsIcon, count: "22" },
      { to: "/standups", label: "Standups", icon: MessageSquareIcon, badge: "9" },
    ],
  },
  {
    title: "Delivery",
    items: [
      { to: "/projects", label: "Projects", icon: LayersIcon },
      { to: "/timesheets", label: "Timesheets", icon: ClockIcon, badge: "4" },
      { to: "/people", label: "People", icon: UsersIcon },
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
      product={{ name: "PulseBoard", tagline: "Delivery workspace" }}
      search={{
        label: "Search projects",
        placeholder: "Search projects",
        to: "/projects",
      }}
      {...(onNavigate ? { onNavigate } : {})}
    />
  );
}
