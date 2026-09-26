import { BoardIcon, BriefcaseIcon, FlagIcon, GanttIcon, HomeIcon, UsersIcon } from "@opsgrids/ui";
import type { NavEntry, NavGroup } from "@opsgrids/ui";

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
 * This product's navigation, for `AppShell`'s `nav` prop.
 *
 * Reports, Integrations, Settings and Help are not here: none of them has a screen with a backend
 * behind it, nothing else in the app links to them, and a nav item that always leads to "Coming
 * soon" is a dead control (CLAUDE.md, "no dead controls").
 */
export function useNavGroups(): { groups: NavGroup[]; pinned: NavEntry[] } {
  return { groups: GROUPS, pinned: [] };
}
