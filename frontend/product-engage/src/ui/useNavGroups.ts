import { usePermissionStatus } from "@opsgrids/client";
import {
  BarChartIcon,
  ClipboardCheckIcon,
  FileTextIcon,
  GlobeIcon,
  HomeIcon,
  InboxIcon,
  LayersIcon,
  MailIcon,
  MegaphoneIcon,
  MessageSquareIcon,
  PlugIcon,
  SettingsIcon,
  ShieldIcon,
  SparkIcon,
  UserPlusIcon,
  UsersIcon,
} from "@opsgrids/ui";
import type { NavEntry, NavGroup } from "@opsgrids/ui";

/**
 * The provider connections, the domains an organization has proven, the named senders mail may go
 * out as, the Cloud API account and its templates for WhatsApp, who Engage may reach — contacts,
 * the lists a campaign is addressed to and the addresses it may never mail — the inbox, and the
 * campaigns themselves, each with its own delivery report.
 *
 * Inbox sits in the top-level Engage group rather than under WhatsApp: it is a place someone
 * works from all day, not a setup step done once, and the top group is where Overview — the
 * other place worked from daily — already lives.
 *
 * The three Email entries are the three setup steps in the order they have to happen: there is
 * nothing to prove without a connection, and no sender to name without a proven domain. WhatsApp
 * has no domain to prove, so it is two steps: connect the account, then track what Meta has
 * approved to send through it. Audience is independent of both — contacts, lists and suppressions
 * exist whether or not a sender is usable yet. Campaigns come last because they need all of it.
 */
const GROUPS: NavGroup[] = [
  {
    title: "Communication",
    items: [
      { to: "/", label: "Overview", icon: HomeIcon, end: true },
      { to: "/whatsapp", label: "WhatsApp", icon: MessageSquareIcon, end: true },
      { to: "/email", label: "Email", icon: MailIcon, end: true },
      { to: "/inbox", label: "Inbox", icon: InboxIcon },
      { to: "/automations", label: "Automations", icon: SparkIcon },
      { to: "/setup", label: "Get started", icon: ClipboardCheckIcon },
    ],
  },
  {
    title: "Email",
    items: [
      { to: "/connection", label: "Connections", icon: PlugIcon },
      { to: "/domains", label: "Domains", icon: GlobeIcon },
      { to: "/senders", label: "Senders", icon: UsersIcon },
      { to: "/email/templates", label: "Email templates", icon: FileTextIcon },
      { to: "/email/settings", label: "Email settings", icon: SettingsIcon },
    ],
  },
  {
    title: "WhatsApp",
    items: [
      { to: "/whatsapp/connection", label: "Connection", icon: MessageSquareIcon },
      { to: "/whatsapp/templates", label: "Templates", icon: FileTextIcon },
      { to: "/whatsapp/analytics", label: "Analytics", icon: BarChartIcon },
      { to: "/whatsapp/settings", label: "Settings", icon: SettingsIcon },
    ],
  },
  {
    title: "Audience",
    items: [
      { to: "/contacts", label: "Contacts", icon: UserPlusIcon },
      { to: "/lists", label: "Lists", icon: LayersIcon },
      { to: "/suppressions", label: "Suppressions", icon: ShieldIcon },
    ],
  },
  {
    title: "Campaigns",
    items: [{ to: "/campaigns", label: "Campaigns", icon: MegaphoneIcon }],
  },
];

/**
 * This product's navigation, for `AppShell`'s `nav` prop.
 *
 * Lists and Campaigns are hidden from a member **known** not to hold their read permission, and
 * shown while that is not yet known — a rail that flashes entries in and out as grants arrive is
 * worse than one that briefly offers a page whose own guard then explains itself. Hiding is
 * courtesy, not protection: both routes carry their own `RequireGrant`, and the service refuses
 * every read behind them regardless of what this rail shows.
 */
export function useNavGroups(): { groups: NavGroup[]; pinned: NavEntry[] } {
  const listsRead = usePermissionStatus("engage.audience.list.read", "ORG");
  const campaignsRead = usePermissionStatus("engage.campaign.campaign.read", "ORG");
  const hidden = new Set([
    ...(listsRead === "denied" ? ["/lists"] : []),
    ...(campaignsRead === "denied" ? ["/campaigns"] : []),
  ]);
  const groups = GROUPS.map((group) => ({
    ...group,
    items: group.items.filter((item) => !hidden.has(item.to)),
  })).filter((group) => group.items.length > 0);

  // Nothing is pinned. Settings and Help are the two entries every other app pins, and neither
  // screen exists here yet.
  return { groups, pinned: [] };
}
