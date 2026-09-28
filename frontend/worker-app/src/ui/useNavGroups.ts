import { HomeIcon, UserIcon } from "@opsgrids/ui";
import type { NavEntry, NavGroup } from "@opsgrids/ui";

// Task detail, scan and pick/pack confirmation are real screens now (docs/fulfilment/plan.md F3)
// but stay out of top-level nav on purpose: each is reached by drilling into a specific task or
// order from Home, the same way an order detail screen is never a nav item of its own. A phone's
// bottom nav is two destinations: the task list, and the person using it.
const GROUPS: NavGroup[] = [
  {
    title: "Main Menu",
    items: [
      { to: "/", label: "Home", icon: HomeIcon, end: true },
      { to: "/profile", label: "Profile", icon: UserIcon },
    ],
  },
];

/** This app's navigation, for `AppShell`'s `nav` prop. */
export function useNavGroups(): { groups: NavGroup[]; pinned: NavEntry[] } {
  return { groups: GROUPS, pinned: [] };
}
