import { useAuth } from "@opsgrids/client";
import {
  AlertTriangleIcon,
  BellIcon,
  BoxIcon,
  FileTextIcon,
  GridIcon,
  HomeIcon,
  KeyIcon,
  LifeBuoyIcon,
  PlugIcon,
  RefreshIcon,
  SettingsIcon,
  Sidebar as SidebarShell,
  TicketIcon,
  UserPlusIcon,
} from "@opsgrids/ui";
import type { NavEntry, NavGroup } from "@opsgrids/ui";
import { INCIDENTS, REQUESTS } from "@/api/fixtures";
import { openIncidentCount, requestsAwaitingApproval } from "@/data/derive";
import { LogoMark } from "./LogoMark";

// Badge counts derive from the fixture dataset so the nav reflects the same numbers the screens
// show. When wired live these graduate to a lightweight summary endpoint.
const OPEN_INCIDENTS = String(openIncidentCount(INCIDENTS));
const AWAITING_APPROVAL = String(requestsAwaitingApproval(REQUESTS));

const GROUPS: NavGroup[] = [
  {
    title: "Service Desk",
    items: [
      { to: "/", label: "Overview", icon: HomeIcon, end: true },
      { to: "/incidents", label: "Incidents", icon: AlertTriangleIcon, count: OPEN_INCIDENTS },
      { to: "/requests", label: "Service Requests", icon: TicketIcon, badge: AWAITING_APPROVAL },
      { to: "/notifications", label: "Notifications", icon: BellIcon, badge: "6" },
    ],
  },
  {
    title: "Service Management",
    items: [
      { to: "/catalog", label: "Service Catalog", icon: GridIcon },
      { to: "/changes", label: "Changes", icon: RefreshIcon },
    ],
  },
  {
    title: "Assets & Config",
    items: [
      { to: "/assets", label: "Assets (CMDB)", icon: BoxIcon },
      { to: "/licenses", label: "Software Licenses", icon: KeyIcon },
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

const PINNED: NavEntry[] = [
  { to: "/settings", label: "Settings", icon: SettingsIcon },
  { to: "/help", label: "Help & Getting Started", icon: LifeBuoyIcon },
];

/**
 * This product's navigation. The shell — account switcher, ⌘K search, active pill, workspace card
 * and theme toggle — is `@opsgrids/ui`'s `Sidebar`. Everything below is the part that is genuinely
 * this product's: its groups, its mark and its wording.
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
      pinned={PINNED}
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
