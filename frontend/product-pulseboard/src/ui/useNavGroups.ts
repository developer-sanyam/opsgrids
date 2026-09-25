import { ClockIcon, ColumnsIcon, GaugeIcon, LayersIcon, MessageSquareIcon, UsersIcon } from "@opsgrids/ui";
import type { NavEntry, NavGroup } from "@opsgrids/ui";

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
 * This product's navigation, for `AppShell`'s `nav` prop.
 *
 * Reports, Integrations, Team Members, Settings and Help are not here: none of them has a screen
 * with a backend behind it, nothing else in the app links to them, and a nav item that always
 * leads to "Coming soon" is a dead control (CLAUDE.md, "no dead controls").
 */
export function useNavGroups(): { groups: NavGroup[]; pinned: NavEntry[] } {
  return { groups: GROUPS, pinned: [] };
}
