/**
 * Where the console's data client and the operator API published in `libs/contracts/openapi` agree,
 * and — pinned as facts, not hoped about — where they do not.
 *
 * The console's screens were built ahead of the staff read API (`services/console`, `billing`,
 * `catalog`, merged as Q1), around view models that assumed richer answers than it gives. This
 * slice moved the console onto the shared packages and reconciled operator **sign-in** with the
 * contract field for field (`auth/contract.test.ts`); it did not reconcile these reads, because
 * doing so needs API changes it may not make — an organization list that carries names, a catalog
 * read, override kinds the console offers and the API does not have.
 *
 * So each divergence below is asserted against the OpenAPI document. That makes the list
 * self-maintaining in the direction that matters: the day the backend ships a route or a field one
 * of these says is missing, the test fails and names the line to delete — and a line that is still
 * true is a line nobody has to rediscover. `api/shape.ts` is what stops a screen crashing on the
 * divergence until then.
 */

import { describe, expect, it } from "vitest";

import { hasOperation, openApi, operationOf, refName, schemaOf } from "@/contracts";
import type { OpenApiDocument } from "@/contracts";

const consoleApi: OpenApiDocument = openApi("console");
const billing: OpenApiDocument = openApi("billing");
const catalog: OpenApiDocument = openApi("catalog");

function propertyNames(document: OpenApiDocument, schema: string): string[] {
  return Object.keys(schemaOf(document, schema).properties ?? {}).sort();
}

function queryParameters(document: OpenApiDocument, path: string): string[] {
  return (operationOf(document, "get", path).parameters ?? [])
    .filter((parameter) => parameter.in === "query")
    .map((parameter) => parameter.name)
    .sort();
}

function responseSchema(document: OpenApiDocument, method: string, path: string): string | null {
  return refName(operationOf(document, method, path).responses["200"]?.content?.["application/json"]?.schema);
}

describe("the routes the console calls", () => {
  it("are published, for every read but the catalog", () => {
    expect(hasOperation(consoleApi, "get", "/api/v1/admin/metrics/overview")).toBe(true);
    expect(hasOperation(consoleApi, "get", "/api/v1/admin/organizations")).toBe(true);
    expect(hasOperation(consoleApi, "get", "/api/v1/admin/organizations/{organization_id}")).toBe(true);
    expect(hasOperation(consoleApi, "get", "/api/v1/admin/subscriptions")).toBe(true);
    expect(hasOperation(consoleApi, "get", "/api/v1/admin/audit")).toBe(true);
    expect(hasOperation(consoleApi, "post", "/api/v1/admin/overrides")).toBe(true);
    expect(hasOperation(billing, "get", "/api/v1/admin/credit-ledger")).toBe(true);
  });

  it("do not include the catalog: there is a staff catalog WRITE surface and no READ", () => {
    // ADR-0003's addendum says the same in its role matrix: "there is no such route today, only the
    // writes". The Plans screen reads `GET /admin/catalog`, which no service publishes.
    for (const document of [consoleApi, billing, catalog]) {
      expect(hasOperation(document, "get", "/api/v1/admin/catalog")).toBe(false);
    }
    const writes = Object.keys(catalog.paths).filter((path) => path.startsWith("/api/v1/admin/catalog"));
    expect(writes.length).toBeGreaterThan(0);
    for (const path of writes) {
      for (const method of Object.keys(catalog.paths[path] ?? {})) {
        expect(["post", "patch"], `${method} ${path}`).toContain(method);
      }
    }
  });
});

describe("what the operator API answers, against what the screens read", () => {
  it("the organization list is {organizations, metrics} — not a page, and with no name or slug on a row", () => {
    expect(responseSchema(consoleApi, "get", "/api/v1/admin/organizations")).toBe("OrganizationsOut");
    expect(propertyNames(consoleApi, "OrganizationsOut")).toEqual(["metrics", "organizations"]);
    // The console's `OrganizationRow` reads `name` and `slug`; the row here has neither. They are in
    // the detail route's directory composite, one organization at a time.
    const row = propertyNames(consoleApi, "OrgStateOut");
    expect(row).not.toContain("name");
    expect(row).not.toContain("slug");
    expect(propertyNames(consoleApi, "OrgDetailOut")).toEqual(
      expect.arrayContaining(["name", "slug", "status", "country"]),
    );
  });

  it("the list takes no search or filter — the console's q, status and bespoke are not parameters", () => {
    expect(queryParameters(consoleApi, "/api/v1/admin/organizations")).toEqual([]);
    expect(queryParameters(consoleApi, "/api/v1/admin/subscriptions")).toEqual([]);
    expect(queryParameters(consoleApi, "/api/v1/admin/audit")).toEqual([]);
  });

  it("subscriptions and the audit log are {subscriptions} and {audit}, and their rows carry no organization name or actor", () => {
    expect(propertyNames(consoleApi, "SubscriptionsOut")).toEqual(["subscriptions"]);
    expect(propertyNames(consoleApi, "AuditListOut")).toEqual(["audit"]);
    expect(propertyNames(consoleApi, "SubscriptionOut")).not.toContain("organization_name");
    // `actor_id` is a UUID; the console shows an operator's name and email.
    const audit = propertyNames(consoleApi, "AuditOut");
    expect(audit).toContain("actor_id");
    expect(audit).not.toContain("actor");
    expect(audit).not.toContain("summary");
  });

  it("the platform metrics carry no history — no delta, sparkline, series or period revenue", () => {
    const overview = propertyNames(consoleApi, "MetricsOverviewOut");
    for (const member of ["mrr_delta_pct", "mrr_sparkline", "mrr_series", "revenue_period_minor", "period_label"]) {
      expect(overview, member).not.toContain(member);
    }
    expect(overview).toEqual(expect.arrayContaining(["mrr_minor", "organizations_by_lifecycle", "trial_conversion_rate"]));
  });

  it("the credit ledger shares a page's items and total, but its entries are a different shape", () => {
    expect(propertyNames(billing, "Page_CreditLedgerEntryAdminOut_")).toEqual(["items", "page", "pages", "size", "total"]);
    const entry = propertyNames(billing, "CreditLedgerEntryAdminOut");
    // The console reads entry_id / kind / organization_name / is_comp; the API has id / entry_type
    // and no organization name and no complimentary flag.
    expect(entry).toEqual(expect.arrayContaining(["id", "entry_type", "amount_minor", "reason", "valid_from", "valid_until"]));
    for (const member of ["entry_id", "kind", "organization_name", "is_comp", "actor"]) {
      expect(entry, member).not.toContain(member);
    }
    // It filters by organization, currency and entry type — not by the console's q or comp.
    expect(queryParameters(billing, "/api/v1/admin/credit-ledger")).toEqual(["currency", "entry_type", "organization_id", "page", "size"]);
  });

  it("the organization detail is identity and one commercial snapshot — no subscriptions, invoices or ledger", () => {
    expect(propertyNames(consoleApi, "OrgDetailOut")).toEqual(["commercial", "country", "created_at", "name", "organization_id", "slug", "status"]);
    // Usage is its own route; invoices have no staff route at all.
    expect(hasOperation(consoleApi, "get", "/api/v1/admin/usage")).toBe(true);
    expect(Object.keys(consoleApi.paths).some((path) => path.includes("invoice"))).toBe(false);
  });
});

describe("what the operator API accepts as an override, against what the console offers", () => {
  const overrideIn = () => schemaOf(consoleApi, "OverrideIn");

  it("has two actions — suspend and comp_grant — where the console offers five", () => {
    expect(overrideIn().properties?.action?.enum).toEqual(["suspend", "comp_grant"]);
    // Reactivate, bespoke price and plan migration have no override; a bespoke price is, in the
    // API's own words, a `restricted_to_org_id` version drafted through the catalog write surface.
    expect(operationOf(catalog, "post", "/api/v1/admin/catalog/plans/{plan_id}/versions").summary).toContain("bespoke");
  });

  it("names its action `action`, not `kind`, and a suspension needs the subscription to suspend", () => {
    const fields = propertyNames(consoleApi, "OverrideIn");
    expect(fields).toContain("action");
    expect(fields).not.toContain("kind");
    expect(fields).toContain("subscription_id");
    expect(overrideIn().required).toEqual(["action", "organization_id"]);
  });

  it("has no complimentary flag and no expiry on a grant, and takes only a non-negative amount", () => {
    const fields = propertyNames(consoleApi, "OverrideIn");
    expect(fields).not.toContain("is_comp");
    expect(fields).not.toContain("valid_until");
    // A reversal is a NEGATIVE grant in the console; the API refuses a negative amount.
    const amount = overrideIn().properties?.amount_minor?.anyOf?.find((variant) => variant.type === "integer");
    expect(amount?.minimum).toBe(0);
  });

  it("answers with what was delegated and to whom — no audit row", () => {
    expect(propertyNames(consoleApi, "OverrideOut")).toEqual(["action", "delegated_to", "organization_id", "result"]);
  });
});
