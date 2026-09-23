import { useAuth, usePermissionStatus } from "@opsgrids/client";
import {
  FileTextIcon,
  GlobeIcon,
  HomeIcon,
  InboxIcon,
  LayersIcon,
  MegaphoneIcon,
  MessageSquareIcon,
  PlugIcon,
  ShieldIcon,
  Sidebar as SidebarShell,
  UserPlusIcon,
  UsersIcon,
} from "@opsgrids/ui";
import type { NavEntry, NavGroup } from "@opsgrids/ui";

import { LogoMark } from "./LogoMark";

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
 *
 * Lists and Campaigns are hidden from a member **known** not to hold their read permission, and
 * shown while that is not yet known — a rail that flashes entries in and out as grants arrive is
 * worse than one that briefly offers a page whose own guard then explains itself. Hiding is
 * courtesy, not protection: both routes carry their own `RequireGrant`, and the service refuses
 * every read behind them regardless of what this rail shows.
 */
const GROUPS: NavGroup[] = [
  {
    title: "Engage",
    items: [
      { to: "/", label: "Overview", icon: HomeIcon, end: true },
      { to: "/inbox", label: "Inbox", icon: InboxIcon },
    ],
  },
  {
    title: "Email",
    items: [
      { to: "/connection", label: "Connections", icon: PlugIcon },
      { to: "/domains", label: "Domains", icon: GlobeIcon },
      { to: "/senders", label: "Senders", icon: UsersIcon },
    ],
  },
  {
    title: "WhatsApp",
    items: [
      { to: "/whatsapp/connection", label: "Connection", icon: MessageSquareIcon },
      { to: "/whatsapp/templates", label: "Templates", icon: FileTextIcon },
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

/** Nothing is pinned. Settings and Help are the two entries every other app pins, and neither
 *  screen exists here yet — the rule above applies to the pinned rail as much as to the groups. */
const PINNED: NavEntry[] = [];

/**
 * This product's navigation. The shell — account switcher, ⌘K search, active pill, workspace card
 * and theme toggle — is `@opsgrids/ui`'s `Sidebar`. Everything below is the part that is genuinely
 * this product's: its groups, its mark and its wording.
 *
 * The search lands on Domains, which reads the `?q=` the shell appends. It is pointed at the one
 * list long enough to need searching, and at a screen that actually honours the query — the shell
 * navigates whatever `to` says, so a `to` whose screen ignores `q` is a search box that silently
 * does nothing.
 */
export function Sidebar({ onNavigate }: { onNavigate?: () => void }) {
  const { user, logout } = useAuth();
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
  const name = user?.full_name ?? "Account";
  const org =
    user?.organizations.find((o) => o.organization_id === user.organization_id) ??
    user?.organizations[0] ??
    null;

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
      product={{ name: "OpsGrids Engage", tagline: "Your own sending identity" }}
      search={{
        label: "Search sending domains",
        placeholder: "Search domains",
        to: "/domains",
      }}
      {...(onNavigate ? { onNavigate } : {})}
    />
  );
}
