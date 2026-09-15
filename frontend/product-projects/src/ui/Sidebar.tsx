import { useAuth } from "@opsgrids/client";
import {
  BarChartIcon,
  BoardIcon,
  BriefcaseIcon,
  FlagIcon,
  GanttIcon,
  HomeIcon,
  LifeBuoyIcon,
  PlugIcon,
  SettingsIcon,
  Sidebar as SidebarShell,
  UserPlusIcon,
} from "@opsgrids/ui";
import type { NavEntry, NavGroup } from "@opsgrids/ui";
import { LogoMark } from "./LogoMark";

const GROUPS: NavGroup[] = [
  {
    title: "Deliver",
    items: [
      { to: "/", label: "Overview", icon: HomeIcon, end: true },
      { to: "/timeline", label: "Timeline", icon: GanttIcon },
      { to: "/board", label: "Board", icon: BoardIcon, count: "18" },
    ],
  },
  {
    title: "Portfolio",
    items: [
      { to: "/portfolio", label: "Projects", icon: BriefcaseIcon },
      { to: "/milestones", label: "Milestones", icon: FlagIcon, badge: "4" },
      { to: "/reports", label: "Reports", icon: BarChartIcon },
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
      product={{ name: "OpsGrids Projects", tagline: "Delivery workspace" }}
      search={{
        label: "Search",
        placeholder: "Search projects",
        to: "/portfolio",
      }}
      {...(onNavigate ? { onNavigate } : {})}
    />
  );
}
