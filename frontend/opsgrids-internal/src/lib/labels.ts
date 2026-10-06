/** Display names for the platform's product keys — the console has no live catalog-browsing read
 *  (`api/client.ts`'s module docstring), so this is the same short, stable list every other screen
 *  in this app already knows the products by. Anything not listed falls back to a title-cased key,
 *  never a raw, unreadable slug. */
const PRODUCT_LABELS: Record<string, string> = {
  crm: "CRM",
  engage: "Engage",
  finance: "Finance",
  hrms: "HRMS",
  itsm: "ITSM",
  marketing: "Marketing",
  operations: "Operations",
  projects: "Projects",
  pulseboard: "PulseBoard",
  storefront: "Storefront",
  support: "Support",
  trading: "Trading Desk",
  auctions: "Auctions",
  procure: "Procure",
  suite: "Suite",
};

/** Every product key this console knows by name, for a product filter's options. */
export const KNOWN_PRODUCT_KEYS: readonly string[] = Object.keys(PRODUCT_LABELS).sort();

/** `"crm"` -> `"CRM"`, `"widget_shop"` -> `"Widget shop"` for a key this list doesn't carry. */
export function productLabel(productKey: string): string {
  const known = PRODUCT_LABELS[productKey];
  if (known) return known;
  const spaced = productKey.replace(/_/g, " ");
  return spaced.charAt(0).toUpperCase() + spaced.slice(1);
}

const REGION_NAMES = (() => {
  try {
    return new Intl.DisplayNames(["en"], { type: "region" });
  } catch {
    return null;
  }
})();

/** `"IN"` -> `"India"` via the platform's own `Intl.DisplayNames` — no country-name package to
 *  bundle. Falls back to the bare code where the runtime has no region display names at all. */
export function regionLabel(countryCode: string): string {
  return REGION_NAMES?.of(countryCode) ?? countryCode;
}

/** The five lifecycle buckets `OrgStateOut.lifecycle` and `LifecycleCountsOut` use, in the
 *  product's own words — the account-service directory `status` field uses a different, larger
 *  vocabulary (`active`, `trial`, `past_due`, `suspended`, `cancelled`) that this dashboard never
 *  reads directly. */
const LIFECYCLE_LABELS: Record<string, string> = {
  active: "Active",
  trial: "Trial",
  suspended: "Suspended",
  churned: "Churned",
  none: "No subscription",
};

export function lifecycleLabel(lifecycle: string): string {
  return LIFECYCLE_LABELS[lifecycle] ?? lifecycle;
}
