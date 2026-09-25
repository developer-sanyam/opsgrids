import { GaugeIcon, HomeIcon, InboxIcon } from "@opsgrids/ui";
import type { NavEntry, NavGroup } from "@opsgrids/ui";

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
 * This product's navigation, for `AppShell`'s `nav` prop.
 *
 * Customers, Topics & Tags, Reports, Integrations, Team Members, Settings and Help all had
 * "Coming soon" screens behind them and no backend to build against this round — removed rather
 * than left as dead links (`docs/loop/crud-audit/support.md`, gap 11). Agent administration and
 * the SLA policy — the two things Team Members and Settings would have covered — live on the
 * "Agents & SLA" screen instead of behind their own placeholder.
 */
export function useNavGroups(): { groups: NavGroup[]; pinned: NavEntry[] } {
  return { groups: GROUPS, pinned: [] };
}
