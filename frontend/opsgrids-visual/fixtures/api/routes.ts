/**
 * What each app's pages ask the gateway, and which fixture answers. Every entry names the OpenAPI
 * operation it stands in for; `fixtures/contracts.test.ts` holds each payload to that operation's
 * response schema, so these can only say what the real services could say.
 *
 * The data describes one fictional organization ("Acme Industries") on 15 Sep 2026, the harness's
 * frozen "now" (`lib/env.ts`). Nothing here is imported by an app — it exists only for the
 * browser the harness drives.
 */

import type { MockRoute } from "../../lib/api";

/** What every customer app asks first: who is signed in, and what they may do. */
function session(app: "crm" | "dashboard"): MockRoute[] {
  return [
    {
      method: "GET",
      path: "/api/v1/auth/me",
      fixture: `${app}/me.json`,
      contract: { spec: "auth", path: "/api/v1/auth/me" },
    },
    {
      method: "GET",
      path: "/api/v1/members/me/grants",
      fixture: `${app}/grants.json`,
      contract: { spec: "account", path: "/api/v1/members/me/grants" },
    },
  ];
}

export const CRM_ROUTES: MockRoute[] = [
  ...session("crm"),
  {
    method: "GET",
    path: "/api/v1/customers/owners",
    fixture: "crm/owners.json",
    contract: { spec: "customer", path: "/api/v1/customers/owners" },
  },
  {
    method: "GET",
    path: "/api/v1/customers",
    fixture: "crm/companies.json",
    contract: { spec: "customer", path: "/api/v1/customers" },
  },
  {
    method: "GET",
    path: "/api/v1/contacts",
    fixture: "crm/contacts.json",
    contract: { spec: "customer", path: "/api/v1/contacts" },
  },
  {
    method: "GET",
    path: "/api/v1/deals",
    fixture: "crm/deals.json",
    contract: { spec: "deal", path: "/api/v1/deals" },
  },
  {
    // The list view pages 25 at a time; the board and the overview ask for the whole book.
    method: "GET",
    path: "/api/v1/deals",
    query: { size: "25" },
    fixture: "crm/deals-list.json",
    contract: { spec: "deal", path: "/api/v1/deals" },
  },
  {
    method: "GET",
    path: "/api/v1/deals/summary",
    fixture: "crm/summary.json",
    contract: { spec: "deal", path: "/api/v1/deals/summary" },
  },
  {
    method: "GET",
    path: "/api/v1/deals/activities",
    fixture: "crm/activities.json",
    contract: { spec: "deal", path: "/api/v1/deals/activities" },
  },
];

const PLANNED_PRODUCTS = ["crm", "hrms", "operations", "finance", "projects", "itsm"] as const;

export const DASHBOARD_ROUTES: MockRoute[] = [
  ...session("dashboard"),
  {
    method: "GET",
    path: "/api/v1/products",
    fixture: "dashboard/products.json",
    contract: { spec: "product", path: "/api/v1/products" },
  },
  {
    method: "GET",
    path: "/api/v1/products/activations",
    fixture: "dashboard/activations.json",
    contract: { spec: "product", path: "/api/v1/products/activations" },
  },
  {
    method: "GET",
    path: "/api/v1/subscriptions",
    fixture: "dashboard/subscriptions.json",
    contract: { spec: "subscription", path: "/api/v1/subscriptions" },
  },
  ...PLANNED_PRODUCTS.map(
    (key): MockRoute => ({
      method: "GET",
      path: "/api/v1/plans",
      query: { product_key: key },
      fixture: `dashboard/plans-${key}.json`,
      contract: { spec: "catalog", path: "/api/v1/plans" },
    }),
  ),
];

export const INTERNAL_ROUTES: MockRoute[] = [
  {
    method: "GET",
    path: "/api/v1/auth/operator/me",
    fixture: "internal/me.json",
    contract: { spec: "auth", path: "/api/v1/auth/operator/me" },
  },
  {
    method: "GET",
    path: "/api/v1/admin/metrics/overview",
    fixture: "internal/overview.json",
    contract: { spec: "console", path: "/api/v1/admin/metrics/overview" },
  },
  {
    method: "GET",
    path: "/api/v1/admin/metrics/series",
    fixture: "internal/series.json",
    contract: { spec: "console", path: "/api/v1/admin/metrics/series" },
  },
  {
    method: "GET",
    path: "/api/v1/admin/organizations",
    fixture: "internal/organizations.json",
    contract: { spec: "console", path: "/api/v1/admin/organizations" },
  },
  {
    method: "GET",
    path: "/api/v1/admin/subscriptions",
    fixture: "internal/subscriptions.json",
    contract: { spec: "console", path: "/api/v1/admin/subscriptions" },
  },
  {
    method: "GET",
    path: "/api/v1/admin/audit",
    fixture: "internal/audit.json",
    contract: { spec: "console", path: "/api/v1/admin/audit" },
  },
];

export const ALL_ROUTES: Record<string, MockRoute[]> = {
  crm: CRM_ROUTES,
  dashboard: DASHBOARD_ROUTES,
  internal: INTERNAL_ROUTES,
};
