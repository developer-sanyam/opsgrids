import { HomeIcon, UserIcon } from "@opsgrids/ui";
import type { NavEntry, NavGroup } from "@opsgrids/ui";

// Two real screens today (Home's task-list shell, Profile). Task detail, scan and pick/pack
// confirmation have no backend yet (docs/fulfilment/plan.md F3) and are deliberately not in the
// nav — a nav item whose screen has no backend does not belong in it (frontend/CLAUDE.md: zero
// placeholder or dead code).
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
