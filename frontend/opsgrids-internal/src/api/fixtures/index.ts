/**
 * Fixture barrel: re-exports the domain fixtures and composes the per-org detail payload the way
 * the live `/admin/organizations/{id}` route does — the directory identity from account-service,
 * composed with console's own commercial projection. Nothing here adds a field the live composite
 * doesn't have; subscriptions, usage and the ledger are separate reads in fixtures mode exactly as
 * they are live (see `routes/AccountDetail.tsx`, which fetches and filters them client-side).
 */

import type {
  OrganizationsOut,
  OrgDetailOut,
  OverrideRequest,
  OverrideResult,
  SubscriptionOut,
  SubscriptionsOut,
} from "@/api/types";
import { orgDirectory, orgStateFor, buildOrgStates } from "./orgs";
import { buildPlatformMetrics } from "./metrics";
import { PRODUCT_NAME, planVersionById } from "./catalog";

export * from "./catalog";
export * from "./orgs";
export * from "./ledger";
export * from "./usage";
export * from "./audit";
export * from "./metrics";
export * from "./pricing";
export * from "./planChange";

export function buildOrganizationsOut(): OrganizationsOut {
  return { organizations: buildOrgStates(), metrics: buildPlatformMetrics() };
}

export function buildSubscriptionsOut(): SubscriptionsOut {
  // Re-flattened the same way `services/console/app/api/routes.py::list_subscriptions` builds it:
  // one row per organization × subscribed product, off `subscription_states`, with catalog's
  // display names joined in the same best-effort way (never blanking the rest of the row).
  const rows: SubscriptionOut[] = buildOrgStates().flatMap((state) =>
    Object.entries(state.subscription_states).map(([productKey, entry]) => {
      const rawVersion = entry.plan_version_id ?? null;
      const version = rawVersion ? planVersionById(rawVersion) : undefined;
      return {
        organization_id: state.organization_id,
        name: state.name,
        slug: state.slug,
        product_key: productKey,
        product_name: PRODUCT_NAME[productKey] ?? null,
        state: String(entry.state ?? "unknown"),
        plan_version_id: rawVersion,
        plan_name: version?.plan_name ?? null,
        amount_minor: entry.amount_minor ?? 0,
        currency: entry.currency ?? null,
        trial_ends_at: entry.trial_ends_at ?? null,
      };
    }),
  );
  return { subscriptions: rows };
}

/** Compose the full org-detail payload for one organization, or `null` if unknown. */
export function organizationDetail(organizationId: string): OrgDetailOut | null {
  const directory = orgDirectory(organizationId);
  if (!directory) return null;
  return {
    organization_id: directory.organization_id,
    slug: directory.slug,
    name: directory.name,
    status: directory.status,
    country: directory.country,
    created_at: directory.created_at,
    commercial: orgStateFor(organizationId) ?? null,
  };
}

/**
 * Synthesizes the accepted result a live `POST /admin/overrides` would produce, so the action
 * forms demonstrate the full flow with no admin-gateway. `client.ts` reaches this only through a
 * dynamic `import()` of this module, gated on `USE_FIXTURES` — never a static import — so this
 * fabricated identity can never ship in a production bundle.
 */
export function simulateOverride(request: OverrideRequest): OverrideResult {
  return {
    action: request.action,
    organization_id: request.organization_id,
    delegated_to: request.action === "comp_grant" ? "billing" : "subscription",
    result: { status: "accepted", note: "fixtures mode — not sent to a live service" },
  };
}
