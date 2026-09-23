import { useAuth, useCan } from "@opsgrids/client";
import {
  BoxIcon,
  BuildingIcon,
  GlobeIcon,
  KeyIcon,
  LifeBuoyIcon,
  MapPinIcon,
  NetworkIcon,
  ReceiptIcon,
  SettingsIcon,
  ShieldIcon,
  Sidebar as SidebarShell,
  UsersIcon,
} from "@opsgrids/ui";
import type { NavEntry, NavGroup } from "@opsgrids/ui";

import { LogoMark } from "./LogoMark";

/**
 * The dashboard's navigation. The shell — account, active pill, workspace card, theme toggle —
 * is `@opsgrids/ui`'s `Sidebar`; below is only what is this app's: destinations and wording.
 * "Products" is the marketplace and the home page, because a customer who has just signed up has
 * one job here — start something.
 */
const PRODUCT_GROUP: NavGroup = {
  title: "Your OpsGrids",
  items: [
    { to: "/", label: "Products", icon: BoxIcon, end: true },
    { to: "/subscriptions", label: "Subscriptions", icon: SettingsIcon },
    // `ReceiptIcon`, not `LifeBuoyIcon` — the pinned Help entry below used to share that icon
    // with Invoices, which is confusing when both are visible at once.
    { to: "/invoices", label: "Invoices", icon: ReceiptIcon },
  ],
};

/** Gated per item by the read permission its screen requires (ADR-0013 §4). Hides navigation
 * only — the routes stay reachable by URL, and every endpoint behind them refuses on its own; a
 * hidden nav item is a courtesy, not the boundary. */
const MEMBERS_ITEM: NavEntry = { to: "/admin/members", label: "Members", icon: UsersIcon };
const ROLES_ITEM: NavEntry = { to: "/admin/roles", label: "Roles", icon: ShieldIcon };
const ORGANIZATION_ITEM: NavEntry = { to: "/admin/organization", label: "Organization", icon: BuildingIcon };
const UNITS_ITEM: NavEntry = { to: "/admin/org-units", label: "Org units", icon: NetworkIcon };
const LOCATIONS_ITEM: NavEntry = { to: "/admin/locations", label: "Locations", icon: MapPinIcon };
const DOMAINS_ITEM: NavEntry = { to: "/admin/domains", label: "Domains", icon: GlobeIcon };

const PINNED: NavEntry[] = [
  { to: "/account/security", label: "Security", icon: KeyIcon },
  { to: "/help", label: "Help", icon: LifeBuoyIcon },
];

export function Sidebar({ onNavigate }: { onNavigate?: () => void }) {
  const { user, logout } = useAuth();
  const name = user?.full_name ?? "Account";
  const org =
    user?.organizations.find((o) => o.organization_id === user.organization_id) ??
    user?.organizations[0] ??
    null;

  // Hooks cannot run inside a loop or a `.filter` callback, so each permission is its own call,
  // by name.
  const canReadMembers = useCan("core.identity.membership.read");
  const canReadRoles = useCan("core.identity.role.read");
  const canReadOrganization = useCan("core.org.organization.read");
  const canReadUnits = useCan("core.org.unit.read");
  const canReadLocations = useCan("core.org.location.read");
  const canReadDomains = useCan("core.identity.domain.read");

  const adminItems: NavEntry[] = [
    ...(canReadMembers ? [MEMBERS_ITEM] : []),
    ...(canReadRoles ? [ROLES_ITEM] : []),
    ...(canReadOrganization ? [ORGANIZATION_ITEM] : []),
    ...(canReadUnits ? [UNITS_ITEM] : []),
    ...(canReadLocations ? [LOCATIONS_ITEM] : []),
    ...(canReadDomains ? [DOMAINS_ITEM] : []),
  ];

  const groups: NavGroup[] =
    adminItems.length > 0 ? [PRODUCT_GROUP, { title: "Administration", items: adminItems }] : [PRODUCT_GROUP];

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
      product={{ name: "OpsGrids", tagline: "One account, every product" }}
      search={{ label: "Search products", placeholder: "Search products", to: "/" }}
      {...(onNavigate ? { onNavigate } : {})}
    />
  );
}
