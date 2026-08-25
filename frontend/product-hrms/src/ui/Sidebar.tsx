import { useAuth } from "@atmora/client";
import {
  BellIcon,
  BuildingIcon,
  CalendarCheckIcon,
  ClipboardCheckIcon,
  FileTextIcon,
  HomeIcon,
  LifeBuoyIcon,
  MapPinIcon,
  PlugIcon,
  SettingsIcon,
  Sidebar as SidebarShell,
  UserPlusIcon,
  UsersIcon,
} from "@atmora/ui";
import type { NavEntry, NavGroup } from "@atmora/ui";
import { LogoMark } from "./LogoMark";

const GROUPS: NavGroup[] = [
  {
    title: "Main Menu",
    items: [
      { to: "/", label: "Dashboard", icon: HomeIcon, end: true },
      { to: "/attendance", label: "Attendance", icon: CalendarCheckIcon },
      { to: "/notifications", label: "Notifications", icon: BellIcon, badge: "12" },
      { to: "/tasks", label: "Tasks", icon: ClipboardCheckIcon, count: "5" },
    ],
  },
  {
    title: "Records",
    items: [
      { to: "/employees", label: "Employees", icon: UsersIcon },
      { to: "/departments", label: "Departments", icon: BuildingIcon },
      { to: "/locations", label: "Locations", icon: MapPinIcon },
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
      product={{ name: "Atmora HR", tagline: "People workspace" }}
      search={{
        label: "Search",
        placeholder: "Search",
        to: "/employees",
      }}
      {...(onNavigate ? { onNavigate } : {})}
    />
  );
}
