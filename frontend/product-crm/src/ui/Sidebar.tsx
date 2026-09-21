import { useCallback } from "react";

import { listDeals } from "@/api/client";
import { useAsync, useAuth } from "@opsgrids/client";
import {
  ActivityIcon,
  BuildingIcon,
  FileTextIcon,
  HomeIcon,
  KanbanIcon,
  LifeBuoyIcon,
  PlugIcon,
  SettingsIcon,
  Sidebar as SidebarShell,
  UserPlusIcon,
  UsersIcon,
} from "@opsgrids/ui";
import type { NavEntry, NavGroup } from "@opsgrids/ui";
import { LogoMark } from "./LogoMark";

const PINNED: NavEntry[] = [
  { to: "/settings", label: "Settings", icon: SettingsIcon },
  { to: "/help", label: "Help & Getting Started", icon: LifeBuoyIcon },
];

/**
 * This product's navigation. The shell — account switcher, ⌘K search, active pill, workspace card
 * and theme toggle — is `@opsgrids/ui`'s `Sidebar`. Everything below is the part that is genuinely
 * this product's: its groups, its mark and its wording.
 *
 * The Deals count is real (`total` from the same paged envelope every list screen reads), not the
 * literal `"15"` this used to render. There is no honest source for an "unread" badge on
 * Activities — the timeline has no read/unread concept — so it carries no badge rather than a
 * number that never changes.
 */
export function Sidebar({ onNavigate }: { onNavigate?: () => void }) {
  const { user, logout } = useAuth();
  const name = user?.full_name ?? "Account";
  const org =
    user?.organizations.find((o) => o.organization_id === user.organization_id) ??
    user?.organizations[0] ??
    null;

  const dealsTotal = useAsync(useCallback(() => listDeals({ size: 1 }), []), []);
  const dealsCount = dealsTotal.status === "success" ? String(dealsTotal.data.total) : undefined;

  const groups: NavGroup[] = [
    {
      title: "Pipeline",
      items: [
        { to: "/", label: "Overview", icon: HomeIcon, end: true },
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
        { to: "/reports", label: "Reports", icon: FileTextIcon },
      ],
    },
    {
      title: "Workspace",
      items: [
        { to: "/integrations", label: "Integrations", icon: PlugIcon },
        { to: "/team", label: "Team Members", icon: UserPlusIcon },
      ],
    },
  ];

  return (
    <SidebarShell
      groups={groups}
      pinned={PINNED}
      account={{
        name,
        email: user?.email ?? "",
        ...(org ? { organizationName: org.name } : {}),
      }}
      onSignOut={() => void logout()}
      logo={<LogoMark className="size-8 shrink-0" />}
      product={{ name: "OpsGrids Revenue", tagline: "Revenue workspace" }}
      search={{
        label: "Search deals",
        placeholder: "Search deals",
        to: "/deals",
      }}
      {...(onNavigate ? { onNavigate } : {})}
    />
  );
}
