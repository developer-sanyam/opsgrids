/**
 * Fixture barrel: re-exports the domain fixtures and composes the per-org detail payload the way
 * the console backend's projection would — subscriptions, the entitlement projection, usage,
 * invoices, the credit ledger, and the comp/credit balance as a SUM over in-window entries.
 */

import type {
  Entitlement,
  OrganizationDetail,
  OverrideRequest,
  OverrideResult,
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

/**
 * Synthesizes the accepted result and audit row a live `POST /admin/overrides` would produce, so
 * the action forms demonstrate the full flow with no admin-gateway. `client.ts` reaches this only
 * through a dynamic `import()` of this module, gated on `USE_FIXTURES` — never a static import —
 * so this fabricated identity can never ship in a production bundle.
 */
export function simulateOverride(request: OverrideRequest): OverrideResult {
  return {
    override_id: `ovr_${Math.random().toString(16).slice(2, 10)}`,
    status: "accepted",
    audit: {
      audit_id: `aud_${Math.random().toString(16).slice(2, 8)}`,
      occurred_at: new Date().toISOString(),
      actor: "Priya Venkatesan",
      actor_email: "priya.venkatesan@opsgrids.com",
      action: `override.${request.kind}`,
      organization_id: request.organization_id,
      organization_name: null,
      summary: `Simulated ${request.kind} override (fixtures mode — not sent to a live service)`,
      result: "success",
      target_ref: null,
    },
  };
}
