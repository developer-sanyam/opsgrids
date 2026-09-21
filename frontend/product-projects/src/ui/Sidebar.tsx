import { useAuth } from "@opsgrids/client";
import {
  BoardIcon,
  BriefcaseIcon,
  FlagIcon,
  GanttIcon,
  HomeIcon,
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
    title: "Deliver",
    items: [
      { to: "/", label: "Overview", icon: HomeIcon, end: true },
      { to: "/timeline", label: "Timeline", icon: GanttIcon },
      { to: "/board", label: "Board", icon: BoardIcon },
    ],
  },
  {
    title: "Portfolio",
    items: [
      { to: "/portfolio", label: "Projects", icon: BriefcaseIcon },
      { to: "/milestones", label: "Milestones", icon: FlagIcon },
    ],
  },
  {
    title: "Workspace",
    items: [{ to: "/team", label: "Team", icon: UsersIcon }],
  },
];

/**
 * This product's navigation. The shell — account switcher, ⌘K search, active pill, workspace card
 * and theme toggle — is `@opsgrids/ui`'s `Sidebar`. Everything below is the part that is genuinely
 * this product's: its groups, its mark and its wording.
 *
 * Reports, Integrations, Settings and Help are not here: none of them has a screen with a backend
 * behind it, nothing else in the app links to them, and a nav item that always leads to "Coming
 * soon" is a dead control (CLAUDE.md, "no dead controls").
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
