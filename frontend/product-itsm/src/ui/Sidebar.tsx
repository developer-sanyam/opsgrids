import { useCallback } from "react";

import { getOverview } from "@/api/client";
import { useAsync, useAuth } from "@opsgrids/client";
import {
  AlertTriangleIcon,
  BoxIcon,
  HomeIcon,
  Sidebar as SidebarShell,
  TicketIcon,
} from "@opsgrids/ui";
import type { NavGroup } from "@opsgrids/ui";
import { LogoMark } from "./LogoMark";

/**
 * This product's navigation. The shell — account switcher, ⌘K search, active pill, workspace card
 * and theme toggle — is `@opsgrids/ui`'s `Sidebar`. Everything below is the part that is genuinely
 * this product's: its groups, its mark and its wording.
 *
 * Badge counts come from `GET /itsm/overview` — the same rollup the Overview page renders — not
 * from a capped list or a literal. Every nav entry here has a screen with a real backend behind
 * it; the eight that did not (Notifications, Changes, Licenses, Reports, Integrations, Team,
 * Settings, Help) are gone rather than linking to a placeholder.
 */
export function Sidebar({ onNavigate }: { onNavigate?: () => void }) {
  const { user, logout } = useAuth();
  const overview = useAsync(useCallback(() => getOverview(), []), []);
  const name = user?.full_name ?? "Account";
  const org =
    user?.organizations.find((o) => o.organization_id === user.organization_id) ??
    user?.organizations[0] ??
    null;

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

  return (
    <SidebarShell
      groups={groups}
      pinned={[]}
      account={{
        name,
        email: user?.email ?? "",
        ...(org ? { organizationName: org.name } : {}),
      }}
      onSignOut={() => void logout()}
      logo={<LogoMark className="size-8 shrink-0" />}
      product={{ name: "OpsGrids IT", tagline: "Service desk" }}
      search={{
        label: "Search incidents",
        placeholder: "Search",
        to: "/incidents",
      }}
      {...(onNavigate ? { onNavigate } : {})}
    />
  );
}
