import { useAuth, usePermission } from "@opsgrids/client";
import {
  BuildingIcon,
  CalendarCheckIcon,
  ClipboardListIcon,
  ClockIcon,
  HomeIcon,
  InboxIcon,
  MapPinIcon,
  ReplyIcon,
  SettingsIcon,
  Sidebar as SidebarShell,
  UserPlusIcon,
  UsersIcon,
} from "@opsgrids/ui";
import type { NavEntry, NavGroup } from "@opsgrids/ui";
import { useInboxCount } from "@/lib/useInboxCount";
import { LogoMark } from "./LogoMark";

const MAIN_MENU: NavGroup = {
  title: "Main Menu",
  items: [
    { to: "/", label: "Dashboard", icon: HomeIcon, end: true },
    { to: "/attendance", label: "Attendance", icon: CalendarCheckIcon },
  ],
};

/**
 * This product's navigation. The shell — account switcher, ⌘K search, active pill, workspace card
 * and theme toggle — is `@opsgrids/ui`'s `Sidebar`. Everything below is the part that is genuinely
 * this product's: its groups, its mark and its wording.
 *
 * Every entry here opens a screen backed by a live endpoint, or an honest link-out to the app
 * that owns the real one (Departments, Locations, Team Members — `opsgrids-admin`). Notifications,
 * Tasks, Reports, Integrations, Settings and Help had no backend and no link-out target, so they
 * are gone from here and from the router (audit gap 9).
 */
export function Sidebar({ onNavigate }: { onNavigate?: () => void }) {
  const { user, logout } = useAuth();
  const name = user?.full_name ?? "Account";
  const org =
    user?.organizations.find((o) => o.organization_id === user.organization_id) ??
    user?.organizations[0] ??
    null;

  // The quiet grey count on "Waiting on me" — never omitted on a read failure by hiding the
  // group itself: a 403 here means either a per-principal grant gap or an un-reconciled
  // organization (§2.8), and hiding the entry would make that deploy-order mistake invisible.
  const inboxCount = useInboxCount();
  // §1.2, §2.4 rule 2: "Approval settings" is hidden without `policy.write:ORG` — the one entry
  // in this group the server cannot gate by filtering, so it is hidden client-side instead. The
  // route itself stays reachable by URL either way (§4.G's read-only "No permission" rendering).
  const canConfigureApprovals = usePermission("core.approval.policy.write", "ORG");
  const canReadShifts = usePermission("hrms.time.shift.read", "OWN");
  const approvals: NavGroup = {
    title: "Approvals",
    items: [
      { to: "/approvals", label: "Waiting on me", icon: InboxIcon, ...(inboxCount ? { count: inboxCount } : {}) },
      { to: "/approvals/mine", label: "My requests", icon: ClipboardListIcon },
      { to: "/approvals/delegations", label: "Delegations", icon: ReplyIcon },
      ...(canConfigureApprovals
        ? [{ to: "/approvals/settings", label: "Approval settings", icon: SettingsIcon }]
        : []),
    ],
  };

  const records: NavGroup = {
    title: "Records",
    items: [
      { to: "/employees", label: "Employees", icon: UsersIcon },
      ...(canReadShifts ? ([{ to: "/shifts", label: "Shifts & geo-fences", icon: ClockIcon }] as NavEntry[]) : []),
      { to: "/departments", label: "Departments", icon: BuildingIcon },
      { to: "/locations", label: "Locations", icon: MapPinIcon },
    ],
  };

  const workspace: NavGroup = {
    title: "Workspace",
    items: [{ to: "/team", label: "Team Members", icon: UserPlusIcon }],
  };

  const groups: NavGroup[] = [MAIN_MENU, approvals, records, workspace];

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
      product={{ name: "OpsGrids HR", tagline: "People workspace" }}
      search={{
        label: "Search",
        placeholder: "Search",
        to: "/employees",
      }}
      {...(onNavigate ? { onNavigate } : {})}
    />
  );
}
