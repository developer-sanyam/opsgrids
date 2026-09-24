import {
  BuildingIcon,
  GaugeIcon,
  GiftIcon,
  LayersIcon,
  ShieldIcon,
  Sidebar as SidebarShell,
  TagIcon,
} from "@opsgrids/ui";
import type { NavEntry, NavGroup } from "@opsgrids/ui";

import { useOperatorAuth } from "@/auth/context";
import { roleText } from "@/auth/roles";
import type { Capability } from "@/auth/roles";
import { LogoMark } from "./LogoMark";

interface Destination extends NavEntry {
  /** The capability that admits this screen. The rail hides an entry the operator's roles lack. */
  capability: Capability;
}

const GROUPS: { title: string; items: Destination[] }[] = [
  {
    title: "Console",
    items: [
      { to: "/", label: "Overview", icon: GaugeIcon, end: true, capability: "organizations" },
      { to: "/accounts", label: "Accounts", icon: BuildingIcon, capability: "organizations" },
      { to: "/subscriptions", label: "Subscriptions", icon: LayersIcon, capability: "subscriptions" },
    ],
  },
  {
    title: "Commerce",
    items: [
      { to: "/plans", label: "Plans & Pricing", icon: TagIcon, capability: "catalog" },
      { to: "/grants", label: "Grants & Credits", icon: GiftIcon, capability: "creditLedger" },
    ],
  },
  {
    title: "Governance",
    items: [{ to: "/audit", label: "Audit log", icon: ShieldIcon, capability: "audit" }],
  },
];

/**
 * The console's navigation. The shell — account switcher, ⌘K search, active marker, workspace card
 * and theme toggle — is `@opsgrids/ui`'s `Sidebar`; what is this app's is the groups, the mark and
 * the wording.
 *
 * An entry the operator's roles do not admit is left out, and a group left empty goes with it
 * rather than standing as a bare heading. That is a courtesy: the routes carry their own guard and
 * every endpoint behind them refuses the call on its own.
 */
export function Sidebar({ onNavigate }: { onNavigate?: (() => void) | undefined }) {
  const { operator, logout, can } = useOperatorAuth();
  const groups: NavGroup[] = GROUPS.map((group) => ({
    title: group.title,
    items: group.items.filter((item) => can(item.capability)),
  })).filter((group) => group.items.length > 0);

  return (
    <SidebarShell
      groups={groups}
      pinned={[]}
      account={{
        name: operator?.display_name ?? "Operator",
        email: operator?.email ?? "",
        detail: { label: "Operator roles", value: roleText(operator?.operator_roles ?? []) },
      }}
      onSignOut={() => void logout()}
      logo={<LogoMark className="size-8 shrink-0" />}
      product={{ name: "OpsGrids Internal", tagline: "Cross-tenant control panel" }}
      // Not "Search accounts": the Accounts screen has a search of its own under that name, and two
      // controls with one accessible name on one page are one too many to tell apart by ear.
      search={{ label: "Find an account", placeholder: "Search accounts", to: "/accounts" }}
      {...(onNavigate ? { onNavigate } : {})}
    />
  );
}
