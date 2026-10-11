import { useCallback } from "react";

import { listDeals } from "@/api/client";
import { useAsync } from "@opsgrids/client";
import {
  ActivityIcon,
  BuildingIcon,
  HomeIcon,
  KanbanIcon,
  TargetIcon,
  UserPlusIcon,
  UsersIcon,
} from "@opsgrids/ui";
import type { NavEntry, NavGroup } from "@opsgrids/ui";

/** Nothing is pinned to the rail's foot: every entry above it leads to a screen that exists. */
const PINNED: NavEntry[] = [];

/**
 * This product's navigation, for `AppShell`'s `nav`/`pinned` props. The Deals count is real
 * (`total` from the same paged envelope every list screen reads), not a hardcoded number — there
 * is no honest source for an "unread" badge on Activities, so it carries no badge rather than one
 * that never changes.
 */
export function useNavGroups(): { groups: NavGroup[]; pinned: NavEntry[] } {
  const dealsTotal = useAsync(useCallback(() => listDeals({ size: 1 }), []), []);
  const dealsCount = dealsTotal.status === "success" ? String(dealsTotal.data.total) : undefined;

  const groups: NavGroup[] = [
    {
      title: "Pipeline",
      items: [
        { to: "/", label: "Overview", icon: HomeIcon, end: true },
        { to: "/leads", label: "Leads", icon: TargetIcon },
        {
          to: "/deals",
          label: "Deals",
          icon: KanbanIcon,
          ...(dealsCount !== undefined ? { count: dealsCount } : {}),
        },
        { to: "/activities", label: "Activities", icon: ActivityIcon },
      ],
    },
    {
      title: "Records",
      items: [
        { to: "/companies", label: "Companies", icon: BuildingIcon },
        { to: "/contacts", label: "Contacts", icon: UsersIcon },
      ],
    },
    {
      title: "Workspace",
      items: [
        { to: "/team", label: "Team Members", icon: UserPlusIcon },
      ],
    },
  ];

  return { groups, pinned: PINNED };
}
