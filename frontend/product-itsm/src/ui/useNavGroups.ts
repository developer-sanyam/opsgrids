import { useCallback } from "react";

import { getOverview } from "@/api/client";
import { useAsync } from "@opsgrids/client";
import { AlertTriangleIcon, BoxIcon, HomeIcon, TicketIcon } from "@opsgrids/ui";
import type { NavEntry, NavGroup } from "@opsgrids/ui";

/**
 * This product's navigation, for `AppShell`'s `nav` prop.
 *
 * Badge counts come from `GET /itsm/overview` — the same rollup the Overview page renders — not
 * from a capped list or a literal. Every nav entry here has a screen with a real backend behind
 * it; the eight that did not (Notifications, Changes, Licenses, Reports, Integrations, Team,
 * Settings, Help) are gone rather than linking to a placeholder.
 */
export function useNavGroups(): { groups: NavGroup[]; pinned: NavEntry[] } {
  const overview = useAsync(useCallback(() => getOverview(), []), []);

  const openIncidents = overview.status === "success" ? String(overview.data.open_incidents) : undefined;
  const awaitingApproval =
    overview.status === "success" ? String(overview.data.requests_awaiting_approval) : undefined;

  const groups: NavGroup[] = [
    {
      title: "Service Desk",
      items: [
        { to: "/", label: "Overview", icon: HomeIcon, end: true },
        {
          to: "/incidents",
          label: "Incidents",
          icon: AlertTriangleIcon,
          ...(openIncidents !== undefined ? { count: openIncidents } : {}),
        },
        {
          to: "/requests",
          label: "Service Requests",
          icon: TicketIcon,
          ...(awaitingApproval !== undefined && awaitingApproval !== "0"
            ? { badge: awaitingApproval }
            : {}),
        },
      ],
    },
    {
      title: "Assets & Config",
      items: [{ to: "/assets", label: "Assets (CMDB)", icon: BoxIcon }],
    },
  ];

  return { groups, pinned: [] };
}
