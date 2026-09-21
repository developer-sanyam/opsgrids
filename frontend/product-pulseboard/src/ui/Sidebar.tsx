import { useAuth } from "@opsgrids/client";
import {
  ClockIcon,
  ColumnsIcon,
  GaugeIcon,
  LayersIcon,
  MessageSquareIcon,
  Sidebar as SidebarShell,
  UsersIcon,
} from "@opsgrids/ui";
import type { NavGroup } from "@opsgrids/ui";
import { LogoMark } from "./LogoMark";

// No `count` / `badge` on any entry: this product has no endpoint that rolls one up honestly, and
// a number that doesn't move with the data it names is worse than no number at all (CLAUDE.md,
// "no invented numbers presented as live").
const GROUPS: NavGroup[] = [
  {
    title: "Main Menu",
    items: [
      { to: "/", label: "Overview", icon: GaugeIcon, end: true },
      { to: "/board", label: "Board", icon: ColumnsIcon },
      { to: "/standups", label: "Standups", icon: MessageSquareIcon },
    ],
  },
  {
    title: "Delivery",
    items: [
      { to: "/projects", label: "Projects", icon: LayersIcon },
      { to: "/timesheets", label: "Timesheets", icon: ClockIcon },
      { to: "/people", label: "People", icon: UsersIcon },
    ],
  },
];

/**
 * This product's navigation. The shell — account switcher, ⌘K search, active pill, workspace card
 * and theme toggle — is `@opsgrids/ui`'s `Sidebar`. Everything below is the part that is genuinely
 * this product's: its groups, its mark and its wording.
 *
 * Reports, Integrations, Team Members, Settings and Help are not here: none of them has a screen
 * with a backend behind it, nothing else in the app links to them, and a nav item that always
 * leads to "Coming soon" is a dead control (CLAUDE.md, "no dead controls").
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
      // No Settings or Help screen exists behind this product yet, and nothing else links to
      // them — an empty pinned list rather than two more dead controls.
      pinned={[]}
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
