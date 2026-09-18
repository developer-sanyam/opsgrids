/**
 * Typed operator-console client.
 *
 * **Fixtures-first** (ADR-0004 P6c): `VITE_USE_FIXTURES` defaults on, so the whole console renders
 * from `fixtures/` with no admin-gateway — the console backend is not yet wired. Set
 * `VITE_USE_FIXTURES=false` to talk to the live gateway; every function then calls the documented
 * `/api/v1/admin/*` staff routes (ADR-0003 §2) with the operator token from the session. The two
 * code paths return the same shapes, so switching to live is a flag, not a rewrite.
 */

import { apiRequest } from "./http";
import {
  AUDIT_LOG,
  CREDIT_LEDGER,
  PLANS,
  PLAN_VERSIONS,
  PRODUCTS,
  ORGANIZATIONS,
  SUBSCRIPTIONS,
  buildOverviewMetrics,
  organizationDetail,
} from "./fixtures";
import type {
  AccountStatus,
  AuditEntry,
  CreditLedgerEntry,
  OrganizationDetail,
  OrganizationRow,
  OverrideRequest,
  OverrideResult,
  OverviewMetrics,
  Page,
  Plan,
  PlanVersion,
  Product,
  Subscription,
} from "./types";

/** Default source. Fixtures unless `VITE_USE_FIXTURES=false` pins the console to the live gateway.
 *  Dev builds only: `import.meta.env.DEV` compiles to `false` in production, so a stray
 *  CI/production env var can never ship fixture mode — an app rendering fabricated data
 *  with the "Sample data" markers suppressed. */
export const USE_FIXTURES = import.meta.env.DEV && import.meta.env.VITE_USE_FIXTURES !== "false";

/** True when the console is talking to the real admin-gateway. */
export const LIVE = !USE_FIXTURES;

/** Small artificial delay so loading states are exercised in fixtures mode. */
function settle<T>(value: T, ms = 240): Promise<T> {
  return new Promise((resolve) => setTimeout(() => resolve(value), ms));
}

function matches(haystack: string, needle: string): boolean {
  return haystack.toLowerCase().includes(needle.toLowerCase());
}

/* ----------------------------- overview ----------------------------- */

export async function getOverview(): Promise<OverviewMetrics> {
  if (USE_FIXTURES) return settle(buildOverviewMetrics());
  return apiRequest<OverviewMetrics>("/admin/metrics/overview");
}

/* ----------------------------- accounts ----------------------------- */

export interface ListOrganizationsParams {
  search?: string;
  status?: AccountStatus | "all";
  bespoke?: boolean;
}

export async function listOrganizations(
  params: ListOrganizationsParams = {},
): Promise<Page<OrganizationRow>> {
  if (USE_FIXTURES) {
    const q = params.search?.trim() ?? "";
    const items = ORGANIZATIONS.filter((o) => {
      const matchQuery =
        q === "" ||
        matches(o.name, q) ||
        matches(o.slug, q) ||
        matches(o.primary_product_name, q) ||
        matches(o.region, q);
      const matchStatus = !params.status || params.status === "all" || o.status === params.status;
      const matchBespoke = params.bespoke === undefined || o.is_bespoke === params.bespoke;
      return matchQuery && matchStatus && matchBespoke;
    });
    return settle({ items, total: items.length });
  }

  const query = new URLSearchParams();
  if (params.search?.trim()) query.set("q", params.search.trim());
  if (params.status && params.status !== "all") query.set("status", params.status);
  if (params.bespoke !== undefined) query.set("bespoke", String(params.bespoke));
  return apiRequest<Page<OrganizationRow>>(`/admin/organizations?${query.toString()}`);
}

export async function getOrganization(id: string): Promise<OrganizationDetail> {
  if (USE_FIXTURES) {
    const detail = organizationDetail(id);
    if (!detail) throw new Error(`No organization ${id}`);
    return settle(detail);
  }
  return apiRequest<OrganizationDetail>(`/admin/organizations/${encodeURIComponent(id)}`);
}

/* --------------------------- subscriptions -------------------------- */

export interface ListSubscriptionsParams {
  search?: string;
  state?: AccountStatus | "all";
}

export async function listSubscriptions(
  params: ListSubscriptionsParams = {},
): Promise<Page<Subscription>> {
  if (USE_FIXTURES) {
    const q = params.search?.trim() ?? "";
    const items = SUBSCRIPTIONS.filter((s) => {
      const matchQuery =
        q === "" || matches(s.organization_name, q) || matches(s.product_name, q) || matches(s.plan_name, q);
      const matchState = !params.state || params.state === "all" || s.state === params.state;
      return matchQuery && matchState;
    });
    return settle({ items, total: items.length });
  }

  const query = new URLSearchParams();
  if (params.search?.trim()) query.set("q", params.search.trim());
  if (params.state && params.state !== "all") query.set("state", params.state);
  return apiRequest<Page<Subscription>>(`/admin/subscriptions?${query.toString()}`);
}

/* ------------------------------ catalog ----------------------------- */

export interface CatalogBundle {
  products: Product[];
  plans: Plan[];
  versions: PlanVersion[];
}

export async function getCatalog(): Promise<CatalogBundle> {
  if (USE_FIXTURES) {
    return settle({ products: PRODUCTS, plans: PLANS, versions: PLAN_VERSIONS });
  }
  return apiRequest<CatalogBundle>("/admin/catalog");
}

/* --------------------------- credit ledger -------------------------- */

export interface ListLedgerParams {
  search?: string;
  compOnly?: boolean;
}

export async function listLedger(params: ListLedgerParams = {}): Promise<Page<CreditLedgerEntry>> {
  if (USE_FIXTURES) {
    const q = params.search?.trim() ?? "";
    const items = CREDIT_LEDGER.filter((e) => {
      const matchQuery = q === "" || matches(e.organization_name, q) || matches(e.reason, q);
      const matchComp = !params.compOnly || e.is_comp;
      return matchQuery && matchComp;
    }).sort((a, b) => b.created_at.localeCompare(a.created_at));
    return settle({ items, total: items.length });
  }

  const query = new URLSearchParams();
  if (params.search?.trim()) query.set("q", params.search.trim());
  if (params.compOnly) query.set("comp", "true");
  return apiRequest<Page<CreditLedgerEntry>>(`/admin/credit-ledger?${query.toString()}`);
}

/* ------------------------------- audit ------------------------------ */

export interface ListAuditParams {
  search?: string;
  action?: string | "all";
}

export async function listAudit(params: ListAuditParams = {}): Promise<Page<AuditEntry>> {
  if (USE_FIXTURES) {
    const q = params.search?.trim() ?? "";
    const items = AUDIT_LOG.filter((a) => {
      const matchQuery =
        q === "" ||
        matches(a.summary, q) ||
        matches(a.actor, q) ||
        (a.organization_name ? matches(a.organization_name, q) : false);
      const matchAction = !params.action || params.action === "all" || a.action === params.action;
      return matchQuery && matchAction;
    });
    return settle({ items, total: items.length });
  }

  const query = new URLSearchParams();
  if (params.search?.trim()) query.set("q", params.search.trim());
  if (params.action && params.action !== "all") query.set("action", params.action);
  return apiRequest<Page<AuditEntry>>(`/admin/audit?${query.toString()}`);
}

/* ----------------------------- overrides ---------------------------- */

/**
 * Drive a lifecycle override. Live, this POSTs to `/api/v1/admin/overrides`, which delegates to the
 * owning service's `/internal` API (ADR-0004 P6b). In fixtures mode it synthesizes the accepted
 * result and the audit row the endpoint would write, so the action forms demonstrate the full flow.
 */
export async function postOverride(request: OverrideRequest): Promise<OverrideResult> {
  if (USE_FIXTURES) {
    const result: OverrideResult = {
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
    return settle(result, 500);
  }
  return apiRequest<OverrideResult>("/admin/overrides", { method: "POST", body: request });
}
