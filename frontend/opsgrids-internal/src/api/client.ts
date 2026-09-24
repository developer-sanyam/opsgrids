/**
 * Typed operator-console client.
 *
 * **Fixtures-first**: `VITE_USE_FIXTURES` defaults on, so the whole console renders from
 * `fixtures/` with no admin-gateway. Set `VITE_USE_FIXTURES=false` to talk to the live gateway;
 * every function below then calls a real, shipped `/api/v1/admin/*` staff route (ADR-0003 §2 and
 * its addendum) with the operator token from the session — `/admin/organizations`,
 * `/admin/organizations/{id}`, `/admin/organizations/{id}/pricing`, `/admin/metrics/overview`,
 * `/admin/subscriptions`, `/admin/usage`, `/admin/audit`, `/admin/overrides` (console-service) and
 * `/admin/credit-ledger` (billing-service). Every shape below has been checked field-for-field
 * against the deployed schemas (`services/console/app/schemas.py`,
 * `services/billing/app/schemas.py`, `libs/contracts/openapi/*.json`) — see `types.ts`'s module
 * docstring for what that audit found and what it means for each screen.
 *
 * None of `/admin/organizations`, `/admin/subscriptions`, `/admin/usage` or `/admin/audit` accept a
 * search, status or pagination query parameter — each returns its full cross-tenant set every time,
 * so every list screen below filters client-side over what it got back. `/admin/credit-ledger` is
 * the one exception: it is billing's own real, paginated `Page<T>` and takes `organization_id`,
 * `currency` and `entry_type` filters — no free-text search, so the ledger screen also filters its
 * page client-side for that.
 *
 * There is deliberately no `getCatalog()` here any more: catalog-service's `/admin/catalog` prefix
 * only carries **write** routes (create/patch/retire a plan, create/publish a version) — there is
 * no admin `GET` that lists products, plans or versions. A function that called it would 404 on
 * every read, so the catalog-browsing screen was rebuilt around what the API actually offers
 * instead of pointing at a route that doesn't exist (see `routes/Plans.tsx`).
 *
 * `admin.opsgrids.com` serves this console publicly, so `./fixtures` is loaded only through a
 * dynamic `import()` inside each `if (USE_FIXTURES)` branch below, never a static top-of-file
 * import. `USE_FIXTURES` is always `false` in a production build (see below), which makes every
 * one of those imports provably unreachable there — and unlike a static import, Rollup drops a
 * dynamic import it can prove is never called, chunk and all, rather than shipping it unused. A
 * static `import { ORGANIZATIONS, ... } from "./fixtures"` was tried first and measured to *not*
 * do this: the fixture data (fake org names, a stand-in staff member) still turned up in
 * `dist/assets/*.js` even though the code that reads it could never run — a static import always
 * evaluates the whole module, live-or-not, so nothing about the surrounding `if` matters to it.
 */

import { apiRequest } from "./http";
import type {
  AuditListOut,
  CreditLedgerEntryAdminOut,
  ListLedgerParams,
  OrganizationPricing,
  OrganizationsOut,
  OrgDetailOut,
  OverrideRequest,
  OverrideResult,
  MetricsOverviewOut,
  Page,
  SetPriceRequest,
  SetPriceResult,
  SubscriptionsOut,
  UsageListOut,
} from "./types";

/** Default source. Fixtures unless `VITE_USE_FIXTURES=false` pins the console to the live gateway.
 *  Dev builds only: `import.meta.env.DEV` compiles to `false` in production, so a stray
 *  CI/production env var can never ship fixture mode — an app rendering fabricated data
 *  with the "Sample data" markers suppressed. */
export const USE_FIXTURES = import.meta.env.DEV && import.meta.env.VITE_USE_FIXTURES !== "false";

/** True when the console is talking to the real admin-gateway. */
export const LIVE = !USE_FIXTURES;

/** The only path to `./fixtures` — see the module docstring for why this is a dynamic import. */
function loadFixtures() {
  return import("./fixtures");
}

/** Small artificial delay so loading states are exercised in fixtures mode. */
function settle<T>(value: T, ms = 240): Promise<T> {
  return new Promise((resolve) => setTimeout(() => resolve(value), ms));
}

/* ----------------------------- overview ----------------------------- */

export async function getOverview(): Promise<MetricsOverviewOut> {
  if (USE_FIXTURES) {
    const { buildOverviewMetrics } = await loadFixtures();
    return settle(buildOverviewMetrics());
  }
  return apiRequest<MetricsOverviewOut>("/admin/metrics/overview");
}

/* ----------------------------- accounts ----------------------------- */

/** Every organization's commercial state, plus platform metrics. The server takes no query
 *  params on this route — every caller gets every org, every time. */
export async function listOrganizations(): Promise<OrganizationsOut> {
  if (USE_FIXTURES) {
    const { buildOrganizationsOut } = await loadFixtures();
    return settle(buildOrganizationsOut());
  }
  return apiRequest<OrganizationsOut>("/admin/organizations");
}

export async function getOrganization(id: string): Promise<OrgDetailOut> {
  if (USE_FIXTURES) {
    const { organizationDetail } = await loadFixtures();
    const detail = organizationDetail(id);
    if (!detail) throw new Error(`No organization ${id}`);
    return settle(detail);
  }
  return apiRequest<OrgDetailOut>(`/admin/organizations/${encodeURIComponent(id)}`);
}

/* --------------------------- subscriptions -------------------------- */

/** Every organization's subscriptions, flattened from their commercial snapshots. Unfiltered —
 *  the server accepts no query params here either. */
export async function listSubscriptions(): Promise<SubscriptionsOut> {
  if (USE_FIXTURES) {
    const { buildSubscriptionsOut } = await loadFixtures();
    return settle(buildSubscriptionsOut());
  }
  return apiRequest<SubscriptionsOut>("/admin/subscriptions");
}

/* -------------------------------- usage ------------------------------- */

/** Every organization's usage rollups, by meter and period. Unfiltered. */
export async function listUsage(): Promise<UsageListOut> {
  if (USE_FIXTURES) {
    const { buildUsageListOut } = await loadFixtures();
    return settle(buildUsageListOut());
  }
  return apiRequest<UsageListOut>("/admin/usage");
}

/* --------------------------- credit ledger (billing) ------------------ */

export async function listLedger(
  params: ListLedgerParams = {},
): Promise<Page<CreditLedgerEntryAdminOut>> {
  if (USE_FIXTURES) {
    const { buildLedgerPage } = await loadFixtures();
    return settle(buildLedgerPage(params));
  }

  const query = new URLSearchParams();
  if (params.organizationId) query.set("organization_id", params.organizationId);
  if (params.currency) query.set("currency", params.currency);
  if (params.entryType) query.set("entry_type", params.entryType);
  query.set("page", String(params.page ?? 1));
  query.set("size", String(params.size ?? 200));
  return apiRequest<Page<CreditLedgerEntryAdminOut>>(`/admin/credit-ledger?${query.toString()}`);
}

/* ------------------------------- audit ------------------------------ */

/** Recent operator-override audit rows across every organization. Unfiltered. */
export async function listAudit(): Promise<AuditListOut> {
  if (USE_FIXTURES) {
    const { buildAuditListOut } = await loadFixtures();
    return settle(buildAuditListOut());
  }
  return apiRequest<AuditListOut>("/admin/audit");
}

/* --------------------------- client pricing -------------------------- */

/**
 * `GET /admin/organizations/{id}/pricing` — every product's current price, the public list price
 * for reference, and who-changed-it-when history. `console.pricing.read`.
 */
export async function getOrganizationPricing(organizationId: string): Promise<OrganizationPricing> {
  if (USE_FIXTURES) {
    const { pricingForOrg } = await loadFixtures();
    const pricing = pricingForOrg(organizationId);
    if (!pricing) throw new Error(`No organization ${organizationId}`);
    return settle(pricing);
  }
  return apiRequest<OrganizationPricing>(
    `/admin/organizations/${encodeURIComponent(organizationId)}/pricing`,
  );
}

/**
 * `POST /admin/organizations/{id}/pricing` — clones the product's public flat plan into a version
 * pinned to this organization, at the agreed price, and optionally applies it to their current
 * subscription. `idempotencyKey` is generated once per dialog open and reused on every retry within
 * that same open, so a double click — or a genuine retry after a dropped response — resolves to the
 * one price change, never two. `console.pricing.write`.
 */
export async function setOrganizationPrice(
  organizationId: string,
  request: SetPriceRequest,
  idempotencyKey: string,
): Promise<SetPriceResult> {
  if (USE_FIXTURES) {
    const { applyFixturePrice } = await loadFixtures();
    return settle(applyFixturePrice(organizationId, request), 500);
  }
  return apiRequest<SetPriceResult>(`/admin/organizations/${encodeURIComponent(organizationId)}/pricing`, {
    method: "POST",
    body: request,
    headers: { "Idempotency-Key": idempotencyKey },
  });
}

/* ----------------------------- overrides ---------------------------- */

/**
 * Drive a lifecycle override. Live, this POSTs to `/api/v1/admin/overrides` (console-service),
 * which delegates to the owning service's `/internal/v1` API under a service token (ADR-0003
 * §2). `delegation.py` resolves exactly `suspend` and `comp_grant` — see `OverrideRequest` in
 * `types.ts` for what each requires. In fixtures mode it synthesizes the accepted result so the
 * action forms demonstrate the full flow.
 */
export async function postOverride(request: OverrideRequest): Promise<OverrideResult> {
  if (USE_FIXTURES) {
    const { simulateOverride } = await loadFixtures();
    return settle(simulateOverride(request), 500);
  }
  return apiRequest<OverrideResult>("/admin/overrides", { method: "POST", body: request });
}
