/**
 * Typed operator-console client.
 *
 * **Fixtures-first**: `VITE_USE_FIXTURES` defaults on in a dev build, so the whole console renders
 * from `fixtures/` with no admin-gateway. Set `VITE_USE_FIXTURES=false` to talk to the live
 * gateway; every function below then calls a `/api/v1/admin/*` staff route on the operator
 * audience (ADR-0003 §2 and its addendum) through `operatorRequest`, which is what keeps a tenant
 * token out of these calls and an operator token out of tenant routes.
 *
 * The two code paths return the same view models. `api/contract.test.ts` holds each live path to
 * the committed OpenAPI documents and names, with its reason, every one that does not exist yet.
 */

import { operatorRequest } from "@/auth/operatorRequest";
import { USE_FIXTURES } from "@/config";
import { expectMembers, expectPage } from "./shape";
import type {
  AuditEntry,
  CatalogBundle,
  CreditLedgerEntry,
  ListAuditParams,
  ListLedgerParams,
  ListOrganizationsParams,
  ListSubscriptionsParams,
  OrganizationDetail,
  OrganizationRow,
  OverrideRequest,
  OverrideResult,
  OverviewMetrics,
  Page,
  Subscription,
} from "./types";

export type {
  CatalogBundle,
  ListAuditParams,
  ListLedgerParams,
  ListOrganizationsParams,
  ListSubscriptionsParams,
} from "./types";

/**
 * The fixture answers, loaded on demand. Only a dev build with fixtures on ever calls this: in a
 * production build `USE_FIXTURES` is `false`, the branches that call it are removed, and so are the
 * fixtures (see `fixtureClient.ts` for why that has to be a dynamic import).
 */
const fixtures = () => import("./fixtureClient");

/** The members each screen reads of a whole-page answer; see `shape.ts` for why they are checked. */
const OVERVIEW_MEMBERS = [
  "currency",
  "period_label",
  "mrr_minor",
  "mrr_delta_pct",
  "mrr_sparkline",
  "mrr_series",
  "active_accounts",
  "trials_in_progress",
  "trial_conversion_pct",
  "churn_pct",
  "locked_accounts",
  "suspended_accounts",
  "revenue_period_minor",
  "trial_funnel",
  "churn_breakdown",
] as const;
const DETAIL_MEMBERS = [
  "organization",
  "subscriptions",
  "entitlements",
  "usage",
  "invoices",
  "ledger",
  "comp_balance_minor",
] as const;
const LEDGER_ENTRY_MEMBERS = [
  "entry_id",
  "organization_id",
  "organization_name",
  "amount_minor",
  "currency",
  "kind",
  "reason",
  "valid_from",
  "created_at",
  "is_comp",
] as const;

/** `path` with its query string, or bare when there is nothing to ask — never a dangling `?`. */
function withQuery(path: string, query: URLSearchParams): string {
  const text = query.toString();
  return text === "" ? path : `${path}?${text}`;
}

/* ----------------------------- overview ----------------------------- */

export async function getOverview(): Promise<OverviewMetrics> {
  if (USE_FIXTURES) return (await fixtures()).overview();
  return expectMembers<OverviewMetrics>(
    "/admin/metrics/overview",
    await operatorRequest<unknown>("/admin/metrics/overview"),
    OVERVIEW_MEMBERS,
  );
}

/* ----------------------------- accounts ----------------------------- */

export async function listOrganizations(
  params: ListOrganizationsParams = {},
): Promise<Page<OrganizationRow>> {
  if (USE_FIXTURES) return (await fixtures()).organizations(params);

  const query = new URLSearchParams();
  if (params.search?.trim()) query.set("q", params.search.trim());
  if (params.status && params.status !== "all") query.set("status", params.status);
  if (params.bespoke !== undefined) query.set("bespoke", String(params.bespoke));
  return expectPage<OrganizationRow>(
    "/admin/organizations",
    await operatorRequest<unknown>(withQuery("/admin/organizations", query)),
  );
}

export async function getOrganization(id: string): Promise<OrganizationDetail> {
  if (USE_FIXTURES) return (await fixtures()).organization(id);
  return expectMembers<OrganizationDetail>(
    "/admin/organizations/{id}",
    await operatorRequest<unknown>(`/admin/organizations/${encodeURIComponent(id)}`),
    DETAIL_MEMBERS,
  );
}

/* --------------------------- subscriptions -------------------------- */

export async function listSubscriptions(
  params: ListSubscriptionsParams = {},
): Promise<Page<Subscription>> {
  if (USE_FIXTURES) return (await fixtures()).subscriptions(params);

  const query = new URLSearchParams();
  if (params.search?.trim()) query.set("q", params.search.trim());
  if (params.state && params.state !== "all") query.set("state", params.state);
  return expectPage<Subscription>(
    "/admin/subscriptions",
    await operatorRequest<unknown>(withQuery("/admin/subscriptions", query)),
  );
}

/* ------------------------------ catalog ----------------------------- */

export async function getCatalog(): Promise<CatalogBundle> {
  if (USE_FIXTURES) return (await fixtures()).catalog();
  return expectMembers<CatalogBundle>(
    "/admin/catalog",
    await operatorRequest<unknown>("/admin/catalog"),
    ["products", "plans", "versions"],
  );
}

/* --------------------------- credit ledger -------------------------- */

export async function listLedger(params: ListLedgerParams = {}): Promise<Page<CreditLedgerEntry>> {
  if (USE_FIXTURES) return (await fixtures()).ledger(params);

  const query = new URLSearchParams();
  if (params.search?.trim()) query.set("q", params.search.trim());
  if (params.compOnly) query.set("comp", "true");
  const page = expectPage<CreditLedgerEntry>(
    "/admin/credit-ledger",
    await operatorRequest<unknown>(withQuery("/admin/credit-ledger", query)),
  );
  // `items` and `total` are all the live page shares with the console's own; every row must also
  // carry what the screen reads of an entry, or the page is one this console cannot show.
  for (const entry of page.items) {
    expectMembers<CreditLedgerEntry>("/admin/credit-ledger", entry, LEDGER_ENTRY_MEMBERS);
  }
  return page;
}

/* ------------------------------- audit ------------------------------ */

export async function listAudit(params: ListAuditParams = {}): Promise<Page<AuditEntry>> {
  if (USE_FIXTURES) return (await fixtures()).audit(params);

  const query = new URLSearchParams();
  if (params.search?.trim()) query.set("q", params.search.trim());
  if (params.action && params.action !== "all") query.set("action", params.action);
  return expectPage<AuditEntry>(
    "/admin/audit",
    await operatorRequest<unknown>(withQuery("/admin/audit", query)),
  );
}

/* ----------------------------- overrides ---------------------------- */

/**
 * What `POST /admin/overrides` answers (`OverrideOut`), read into what the console says about an
 * accepted override. **A write is not read strictly:** the request has already landed by the time
 * its answer arrives, so an answer in a shape this console does not expect must not turn an
 * override that took effect into an error message. It says what it can and names no audit row it
 * was not told about.
 */
function acceptedFrom(answer: unknown): OverrideResult {
  const body = typeof answer === "object" && answer !== null ? (answer as Record<string, unknown>) : {};
  if (typeof body.summary === "string") {
    return { status: "accepted", summary: body.summary, audit_id: typeof body.audit_id === "string" ? body.audit_id : null };
  }
  const action = typeof body.action === "string" ? body.action : "override";
  const owner = typeof body.delegated_to === "string" ? ` to ${body.delegated_to}` : "";
  return { status: "accepted", summary: `The ${action} override was delegated${owner}.`, audit_id: null };
}

/**
 * Drive a lifecycle override. Live, this POSTs to `/api/v1/admin/overrides` (console-service),
 * which delegates to the owning service's `/internal/v1` API under a service token (ADR-0003
 * §2). In fixtures mode it synthesizes the accepted result the endpoint would answer with, so the
 * action forms demonstrate the full flow.
 */
export async function postOverride(request: OverrideRequest): Promise<OverrideResult> {
  if (USE_FIXTURES) return (await fixtures()).override(request);
  return acceptedFrom(await operatorRequest<unknown>("/admin/overrides", { method: "POST", body: request }));
}
