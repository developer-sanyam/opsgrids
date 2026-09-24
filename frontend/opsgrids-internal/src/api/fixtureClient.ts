/**
 * The console's data with no gateway: every read and the override, answered from `fixtures/`.
 *
 * It is a module of its own, reached only through a dynamic `import()` behind `USE_FIXTURES`, and
 * that is the point. `USE_FIXTURES` is `import.meta.env.DEV && …`, which compiles to `false` in a
 * production build, so the branch that loads this file is removed and the fixtures — fabricated
 * organizations, contacts and invoices — never reach a bundle an operator downloads. Imported
 * statically instead, they did: fixture modules build their arrays by calling helpers, and a module
 * with calls at its top level cannot be dropped for being unused.
 */

import { ApiError, settle } from "@opsgrids/client";

import {
  AUDIT_LOG,
  CREDIT_LEDGER,
  ORGANIZATIONS,
  PLANS,
  PLAN_VERSIONS,
  PRODUCTS,
  SUBSCRIPTIONS,
  buildOverviewMetrics,
  organizationDetail as detailOf,
} from "./fixtures";
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

function matches(haystack: string, needle: string): boolean {
  return haystack.toLowerCase().includes(needle.toLowerCase());
}

export function overview(): Promise<OverviewMetrics> {
  return settle(buildOverviewMetrics());
}

export function organizations(params: ListOrganizationsParams): Promise<Page<OrganizationRow>> {
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

export function organization(id: string): Promise<OrganizationDetail> {
  const detail = detailOf(id);
  if (!detail) {
    throw new ApiError(404, "No such organization", {
      status: 404,
      title: "Not found",
      detail: "No such organization",
    });
  }
  return settle(detail);
}

export function subscriptions(params: ListSubscriptionsParams): Promise<Page<Subscription>> {
  const q = params.search?.trim() ?? "";
  const items = SUBSCRIPTIONS.filter((s) => {
    const matchQuery =
      q === "" || matches(s.organization_name, q) || matches(s.product_name, q) || matches(s.plan_name, q);
    const matchState = !params.state || params.state === "all" || s.state === params.state;
    return matchQuery && matchState;
  });
  return settle({ items, total: items.length });
}

export function catalog(): Promise<CatalogBundle> {
  return settle({ products: PRODUCTS, plans: PLANS, versions: PLAN_VERSIONS });
}

export function ledger(params: ListLedgerParams): Promise<Page<CreditLedgerEntry>> {
  const q = params.search?.trim() ?? "";
  const items = CREDIT_LEDGER.filter((e) => {
    const matchQuery = q === "" || matches(e.organization_name, q) || matches(e.reason, q);
    const matchComp = !params.compOnly || e.is_comp;
    return matchQuery && matchComp;
  }).sort((a, b) => b.created_at.localeCompare(a.created_at));
  return settle({ items, total: items.length });
}

export function audit(params: ListAuditParams): Promise<Page<AuditEntry>> {
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

/** The accepted answer the endpoint would give, so the action dialogs demonstrate the whole flow. */
export function override(request: OverrideRequest): Promise<OverrideResult> {
  return settle(
    {
      status: "accepted",
      summary: `Simulated ${request.kind} override (fixtures mode — not sent to a live service)`,
      audit_id: `aud_${Math.random().toString(16).slice(2, 8)}`,
    },
    500,
  );
}
