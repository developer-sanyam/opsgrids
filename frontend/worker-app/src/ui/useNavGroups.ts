import { CheckSquareIcon, HomeIcon, UserIcon } from "@opsgrids/ui";
import type { NavEntry, NavGroup } from "@opsgrids/ui";

// Task detail, scan and pick/pack confirmation are real screens now (docs/fulfilment/plan.md F3)
// but stay out of top-level nav on purpose: each is reached by drilling into a specific task or
// order from Home, the same way an order detail screen is never a nav item of its own. Counts
// (ADR-0026, Fulfilment plan F8) is a genuine top-level destination — a session isn't reached by
// drilling into anything else. A phone's bottom nav is three destinations: the task list, counts,
// and the person using it.
const GROUPS: NavGroup[] = [
  {
    title: "Main Menu",
    items: [
      { to: "/", label: "Home", icon: HomeIcon, end: true },
      { to: "/counts", label: "Counts", icon: CheckSquareIcon },
      { to: "/profile", label: "Profile", icon: UserIcon },
    ],
  },
];

/** This app's navigation, for `AppShell`'s `nav` prop. */
export function useNavGroups(): { groups: NavGroup[]; pinned: NavEntry[] } {
  return { groups: GROUPS, pinned: [] };
}
