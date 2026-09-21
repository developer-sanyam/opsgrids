import { useAuth } from "@opsgrids/client";
import {
  FileTextIcon,
  GlobeIcon,
  HomeIcon,
  MessageSquareIcon,
  PlugIcon,
  Sidebar as SidebarShell,
  UsersIcon,
} from "@opsgrids/ui";
import type { NavEntry, NavGroup } from "@opsgrids/ui";

import { LogoMark } from "./LogoMark";

/**
 * Phase 1 of Engage is the sending identity and nothing else: the provider connections, the
 * domains an organization has proven, the named senders mail may go out as, and — for WhatsApp —
 * the Cloud API account and its templates. Campaigns, lists and reporting are later phases and
 * **are deliberately absent from this navigation** — an entry that routes to a "coming soon"
 * panel teaches people the rail lies, and this codebase already has that bug elsewhere. They
 * arrive here when their screens do.
 *
 * The three Email entries are the three setup steps in the order they have to happen: there is
 * nothing to prove without a connection, and no sender to name without a proven domain. WhatsApp
 * has no domain to prove, so it is two steps: connect the account, then track what Meta has
 * approved to send through it.
 */
const GROUPS: NavGroup[] = [
  {
    title: "Engage",
    items: [{ to: "/", label: "Overview", icon: HomeIcon, end: true }],
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
