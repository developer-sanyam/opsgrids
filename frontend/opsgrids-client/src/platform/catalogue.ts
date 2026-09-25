/**
 * The platform product catalogue — one entry per product a customer can start, shared by
 * `usePlatformModules()` so AppShell v2 (`opsgrids-ui`, which cannot import this package) can be
 * handed a nav model rather than reinvent one per app.
 *
 * This mirrors, rather than replaces, the hardcoded product-URL maps the shell audit found in
 * `opsgrids-dashboard/src/routes/Marketplace.tsx`, `product-engage/src/lib/dashboard.ts` and
 * `sso/platformHosts.ts`'s own allowlist: those stay as they are for now (§6), this is the one
 * place a *new* consumer — the shared shell — reads from.
 *
 * Two product keys fold into one entry on purpose:
 * - `crm` also answers to the `sales` entitlement/grant prefix some services still mint under.
 * - `operations` also answers to `inventory` — Operations and Inventory are one console
 *   (`ops.opsgrids.com`), not two nav entries.
 *
 * No `reports` entry: there is no Reports product today, and this file does not invent one.
 */

export type PlatformProductKey =
  | "crm"
  | "operations"
  | "hrms"
  | "finance"
  | "projects"
  | "support"
  | "marketing"
  | "itsm"
  | "storefront"
  | "engage"
  | "pulseboard";

export interface PlatformProduct {
  key: PlatformProductKey;
  displayName: string;
  /** The icon `opsgrids-ui`'s icon set is keyed by — a name, not a component, so this package
   *  never imports the design system. */
  iconKey: string;
  /**
   * Every `ent` claim value and grant-code prefix that answers for this entry. Checked against
   * `AccessClaims.ent` for entitlement and against each grant's `code` (the part before `:SCOPE`)
   * for a held grant — `parsed.code === key || parsed.code.startsWith(`${key}.`)`.
   */
  entitlementKeys: readonly string[];
}

export const PLATFORM_PRODUCTS: readonly PlatformProduct[] = [
  { key: "crm", displayName: "CRM", iconKey: "briefcase", entitlementKeys: ["crm", "sales"] },
  {
    key: "operations",
    displayName: "Operations & Inventory",
    iconKey: "box",
    entitlementKeys: ["operations", "inventory"],
  },
  { key: "hrms", displayName: "HRMS", iconKey: "users", entitlementKeys: ["hrms"] },
  { key: "finance", displayName: "Finance", iconKey: "receipt", entitlementKeys: ["finance"] },
  { key: "projects", displayName: "Projects", iconKey: "network", entitlementKeys: ["projects"] },
  { key: "support", displayName: "Support", iconKey: "life-buoy", entitlementKeys: ["support"] },
  { key: "marketing", displayName: "Marketing", iconKey: "globe", entitlementKeys: ["marketing"] },
  { key: "itsm", displayName: "ITSM", iconKey: "shield", entitlementKeys: ["itsm"] },
  { key: "storefront", displayName: "Storefront", iconKey: "store", entitlementKeys: ["storefront"] },
  { key: "engage", displayName: "Engage", iconKey: "megaphone", entitlementKeys: ["engage"] },
  { key: "pulseboard", displayName: "Pulseboard", iconKey: "activity", entitlementKeys: ["pulseboard"] },
];

/**
 * Production hosts — one named site per product in `infrastructure/edge/Caddyfile`, the same set
 * `sso/platformHosts.ts` allow-lists for redirects. `configurePlatformHosts` overrides these per
 * environment, the same shape `configureAuthOrigin` uses for the auth origin.
 */
const DEFAULT_HOSTS: Record<PlatformProductKey, string> = {
  crm: "https://revenue.opsgrids.com",
  operations: "https://ops.opsgrids.com",
  hrms: "https://people.opsgrids.com",
  finance: "https://finance.opsgrids.com",
  projects: "https://projects.opsgrids.com",
  support: "https://support.opsgrids.com",
  marketing: "https://marketing.opsgrids.com",
  itsm: "https://it.opsgrids.com",
  storefront: "https://store.opsgrids.com",
  engage: "https://engage.opsgrids.com",
  pulseboard: "https://pulse.opsgrids.com",
};

let hosts: Record<PlatformProductKey, string> = { ...DEFAULT_HOSTS };

function stripTrailingSlashes(origin: string): string {
  return origin.replace(/\/+$/, "");
}

/**
 * Call once from the entry module, the same moment each app calls `configureAuthOrigin` — local
 * dev and any non-production environment override the hosts that need it, everything else keeps
 * the production default. Pass only the keys that differ.
 */
export function configurePlatformHosts(overrides: Partial<Record<PlatformProductKey, string>>): void {
  hosts = { ...hosts };
  for (const key of Object.keys(overrides) as PlatformProductKey[]) {
    const value = overrides[key];
    if (value) hosts[key] = stripTrailingSlashes(value);
  }
}

/** Back to the production defaults — test isolation, not a runtime path. */
export function resetPlatformHosts(): void {
  hosts = { ...DEFAULT_HOSTS };
}

/** The configured host origin for a product, no trailing slash. */
export function getPlatformHost(key: PlatformProductKey): string {
  return hosts[key];
}
