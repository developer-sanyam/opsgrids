import { useCan } from "@opsgrids/client";
import {
  ActivityIcon,
  BoxIcon,
  BuildingIcon,
  GlobeIcon,
  KeyIcon,
  LifeBuoyIcon,
  MapPinIcon,
  NetworkIcon,
  PlugIcon,
  ReceiptIcon,
  SettingsIcon,
  ShieldIcon,
  UsersIcon,
} from "@opsgrids/ui";
import type { NavEntry, NavGroup } from "@opsgrids/ui";

/**
 * The dashboard's own navigation, for `AppShell`'s `nav`/`pinned` props. "Products" is the
 * marketplace and the home page, because a customer who has just signed up has one job here —
 * start something.
 *
 * This app's "Administration" group is its own routes (`/admin/*`), gated per item by the read
 * permission its screen requires (ADR-0013 §4) — richer than `useAdminLinks()`'s three, so it
 * stays here rather than moving to `AppShell`'s `adminLinks` prop, which is for every *other* app
 * linking back to this one. Hiding is a courtesy, not the boundary: the routes stay reachable by
 * URL, and every endpoint behind them refuses on its own.
 */
const PRODUCT_GROUP: NavGroup = {
  title: "Your OpsGrids",
  items: [
    { to: "/", label: "Products", icon: BoxIcon, end: true },
    { to: "/subscriptions", label: "Subscriptions", icon: SettingsIcon },
    // `ReceiptIcon`, not `LifeBuoyIcon` — the pinned Help entry below used to share that icon
    // with Invoices, which is confusing when both are visible at once.
    { to: "/invoices", label: "Invoices", icon: ReceiptIcon },
    { to: "/integrations", label: "Integrations", icon: PlugIcon },
  ],
};

const MEMBERS_ITEM: NavEntry = { to: "/admin/members", label: "Members", icon: UsersIcon };
const ROLES_ITEM: NavEntry = { to: "/admin/roles", label: "Roles", icon: ShieldIcon };
const ORGANIZATION_ITEM: NavEntry = { to: "/admin/organization", label: "Organization", icon: BuildingIcon };
const UNITS_ITEM: NavEntry = { to: "/admin/org-units", label: "Org units", icon: NetworkIcon };
const LOCATIONS_ITEM: NavEntry = { to: "/admin/locations", label: "Locations", icon: MapPinIcon };
const DOMAINS_ITEM: NavEntry = { to: "/admin/domains", label: "Domains", icon: GlobeIcon };
const AUDIT_ITEM: NavEntry = { to: "/admin/audit", label: "Audit log", icon: ActivityIcon };

const PINNED: NavEntry[] = [
  { to: "/account/security", label: "Security", icon: KeyIcon },
  { to: "/help", label: "Help", icon: LifeBuoyIcon },
];

export function useNavGroups(): { groups: NavGroup[]; pinned: NavEntry[] } {
  // Hooks cannot run inside a loop or a `.filter` callback, so each permission is its own call,
  // by name.
  const canReadMembers = useCan("core.identity.membership.read");
  const canReadRoles = useCan("core.identity.role.read");
  const canReadOrganization = useCan("core.org.organization.read");
  const canReadUnits = useCan("core.org.unit.read");
  const canReadLocations = useCan("core.org.location.read");
  const canReadDomains = useCan("core.identity.domain.read");
  // ORG, matching the route: the audit read refuses anything narrower.
  const canReadAudit = useCan("core.identity.audit.read", "ORG");

  const adminItems: NavEntry[] = [
    ...(canReadMembers ? [MEMBERS_ITEM] : []),
    ...(canReadRoles ? [ROLES_ITEM] : []),
    ...(canReadOrganization ? [ORGANIZATION_ITEM] : []),
    ...(canReadUnits ? [UNITS_ITEM] : []),
    ...(canReadLocations ? [LOCATIONS_ITEM] : []),
    ...(canReadDomains ? [DOMAINS_ITEM] : []),
    ...(canReadAudit ? [AUDIT_ITEM] : []),
  ];

  const groups: NavGroup[] =
    adminItems.length > 0 ? [PRODUCT_GROUP, { title: "Administration", items: adminItems }] : [PRODUCT_GROUP];

  return { groups, pinned: PINNED };
}
