import { BuildingsIcon, GaugeIcon, GiftIcon, LayersIcon, ShieldIcon, TagIcon } from "./icons";
import type { NavGroup } from "@opsgrids/ui";

/**
 * The operator console's own navigation, for `AppShell`'s `nav` prop. Staff-only: no cross-tenant
 * `modules` group (a customer's products are not this console's concern) and no `adminLinks` (that
 * prop is for a *tenant*'s Users/Settings/Audit Logs — this console has its own Governance group
 * for that instead).
 */
const GROUPS: NavGroup[] = [
  {
    title: "Console",
    items: [
      { to: "/", label: "Overview", icon: GaugeIcon, end: true },
      { to: "/accounts", label: "Accounts", icon: BuildingsIcon },
      { to: "/subscriptions", label: "Subscriptions", icon: LayersIcon },
    ],
  },
  {
    title: "Commerce",
    items: [
      { to: "/plans", label: "Plans & Pricing", icon: TagIcon },
      { to: "/grants", label: "Grants & Credits", icon: GiftIcon },
    ],
  },
  {
    title: "Governance",
    items: [{ to: "/audit", label: "Audit log", icon: ShieldIcon }],
  },
];

export function useNavGroups(): { groups: NavGroup[] } {
  return { groups: GROUPS };
}
