import { useAuth } from "@opsgrids/client";
import { GaugeIcon, HomeIcon, InboxIcon, Sidebar as SidebarShell } from "@opsgrids/ui";
import type { NavGroup } from "@opsgrids/ui";
import { LogoMark } from "./LogoMark";

const GROUPS: NavGroup[] = [
  {
    title: "Support",
    items: [
      { to: "/", label: "Overview", icon: HomeIcon, end: true },
      { to: "/queue", label: "Queue", icon: InboxIcon },
      { to: "/agents", label: "Agents & SLA", icon: GaugeIcon },
    ],
  },
];

/**
 * This product's navigation. The shell — account switcher, ⌘K search, active pill, workspace card
 * and theme toggle — is `@opsgrids/ui`'s `Sidebar`. Everything below is the part that is genuinely
 * this product's: its groups, its mark and its wording.
 *
 * Customers, Topics & Tags, Reports, Integrations, Team Members, Settings and Help all had
 * "Coming soon" screens behind them and no backend to build against this round — removed rather
 * than left as dead links (`docs/loop/crud-audit/support.md`, gap 11). Agent administration and
 * the SLA policy — the two things Team Members and Settings would have covered — live on the
 * "Agents & SLA" screen instead of behind their own placeholder.
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
      pinned={[]}
      account={{
        name,
        email: user?.email ?? "",
        ...(org ? { organizationName: org.name } : {}),
      }}
      onSignOut={() => void logout()}
      logo={<LogoMark className="size-8 shrink-0" />}
      product={{ name: "OpsGrids Support", tagline: "Support workspace" }}
      search={{
        label: "Search tickets",
        placeholder: "Search tickets",
        to: "/queue",
      }}
      {...(onNavigate ? { onNavigate } : {})}
    />
  );
}
