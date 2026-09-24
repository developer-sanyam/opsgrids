/**
 * Fixture barrel: re-exports the domain fixtures and composes the per-org detail payload the way
 * the console backend's projection would — subscriptions, the entitlement projection, usage,
 * invoices, the credit ledger, and the comp/credit balance as a SUM over in-window entries.
 */

import type {
  Entitlement,
  OrganizationDetail,
  Subscription,
} from "@/api/types";
import { PRODUCT_NAME } from "./catalog";
import { coversNow, ledgerForOrg } from "./ledger";
import { invoicesForOrg } from "./invoices";
import { orgById, subscriptionsForOrg } from "./orgs";
import { usageForOrg } from "./usage";
import { daysUntil } from "@/lib/format";

export * from "./catalog";
export * from "./orgs";
export * from "./ledger";
export * from "./invoices";
export * from "./usage";
export * from "./audit";
export * from "./metrics";
export * from "./pricing";

/** The activation projection for one subscription — trial expiry is evaluated at read time. */
function entitlementFor(sub: Subscription): Entitlement {
  const trialLapsed =
    sub.state === "trial" && (daysUntil(sub.trial_ends_at) ?? 1) < 0;
  const entitled =
    sub.state === "active" || (sub.state === "trial" && !trialLapsed);
  return {
    product_key: sub.product_key,
    product_name: PRODUCT_NAME[sub.product_key] ?? sub.product_key,
    entitled,
    state: sub.state,
    expires_at: sub.state === "trial" ? sub.trial_ends_at : null,
  };
}

/** SUM over the org's ledger entries whose validity window covers now (ADR-0004 §3). */
export function compBalanceMinor(organizationId: string): number {
  return ledgerForOrg(organizationId)
    .filter((e) => coversNow(e))
    .reduce((sum, e) => sum + e.amount_minor, 0);
}

/** Compose the full account-detail payload for one organization, or `null` if unknown. */
export function organizationDetail(organizationId: string): OrganizationDetail | null {
  const organization = orgById(organizationId);
  if (!organization) return null;
  const subscriptions = subscriptionsForOrg(organizationId);
  return {
    organization,
    subscriptions,
    entitlements: subscriptions.map(entitlementFor),
    usage: usageForOrg(organizationId),
    invoices: invoicesForOrg(organizationId),
    ledger: ledgerForOrg(organizationId).sort((a, b) =>
      b.created_at.localeCompare(a.created_at),
    ),
    comp_balance_minor: compBalanceMinor(organizationId),
  };
}
