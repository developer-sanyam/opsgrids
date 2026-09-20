/**
 * Typed CRM API client for OpsGrids Revenue.
 *
 * Live by default: every function calls the real gateway. There is no `crm` product segment —
 * `services/customer` and `services/deal` are mounted at `/api/v1/customers`, `/api/v1/contacts`
 * and `/api/v1/deals` with no per-product prefix, so those are the paths used here (same-origin
 * `/api/v1`, proxied to `http://localhost:8100` in dev) with the bearer token from the session.
 * A "company" in this product is a `customer` row in the customer service — `./mappers.ts` is
 * the one explicit seam where that mapping happens; nothing else in the app assumes it.
 *
 * Two things the SPA originally reached for do not exist on the real backend and are not
 * called here: a standalone `/owners` endpoint (the rep directory is `GET /customers/owners` —
 * same data, mounted under customer service) and a `/activities` endpoint (the timeline is
 * `GET /deals/activities` — it lives with the pipeline, not the account book).
 *
 * Set `VITE_USE_FIXTURES=true` to render the whole app from `fixtures.ts` with no gateway and no
 * login — handy for offline design work.
 */

import { apiRequest } from "@opsgrids/client";
import { activityFromWire, companyFromWire, contactFromWire, dealFromWire, mapPage, ownerFromWire } from "./mappers";
import { ACTIVITIES, COMPANIES, CONTACTS, DEALS, OWNERS } from "./fixtures";
import type {
  ActivityOut,
  CompanyOut,
  ContactOut,
  DealOut,
  OwnerOut,
  WirePage,
} from "./wire";
import type {
  Activity,
  ActivityType,
  Company,
  CompanyStatus,
  Contact,
  ContactStatus,
  Deal,
  DealStage,
  Owner,
  Page,
} from "./types";

/** Default source. Live unless `VITE_USE_FIXTURES=true` pins the app to fixtures.
 *  Dev builds only: `import.meta.env.DEV` compiles to `false` in production, so a stray
 *  CI/production env var can never ship fixture mode — an app rendering fabricated data
 *  with the "Sample data" markers suppressed. */
export const USE_FIXTURES = import.meta.env.DEV && import.meta.env.VITE_USE_FIXTURES === "true";

/** True when the app is talking to the real gateway. */
export const LIVE = !USE_FIXTURES;

/** A generous page size — one request returns the whole (small) book for this org. */
const FULL_PAGE = 200;

/** Small artificial delay so loading states are exercised in fixtures mode. */
function settle<T>(value: T, ms = 240): Promise<T> {
  return new Promise((resolve) => setTimeout(() => resolve(value), ms));
}

/* --------------------------------- deals --------------------------------- */

export interface ListDealsParams {
  search?: string;
  stage?: DealStage | "all";
  ownerId?: string | "all";
}

export async function listDeals(params: ListDealsParams = {}): Promise<Page<Deal>> {
  if (USE_FIXTURES) {
    const q = params.search?.trim().toLowerCase() ?? "";
    const filtered = DEALS.filter((d) => {
      const matchesQuery = q === "" || d.name.toLowerCase().includes(q);
      const matchesStage = !params.stage || params.stage === "all" || d.stage === params.stage;
      const matchesOwner =
        !params.ownerId || params.ownerId === "all" || d.owner_id === params.ownerId;
      return matchesQuery && matchesStage && matchesOwner;
    });
    return settle({ items: filtered, total: filtered.length });
  }
  const query = new URLSearchParams({ size: String(FULL_PAGE) });
  if (params.search?.trim()) query.set("q", params.search.trim());
  if (params.stage && params.stage !== "all") query.set("stage", params.stage);
  if (params.ownerId && params.ownerId !== "all") query.set("owner_id", params.ownerId);
  const page = await apiRequest<WirePage<DealOut>>(`/deals?${query.toString()}`);
  return mapPage(page, dealFromWire);
}

/** The whole deal book — powers the Overview KPIs, the funnel and the kanban board. */
export async function getAllDeals(): Promise<Page<Deal>> {
  if (USE_FIXTURES) return settle({ items: DEALS, total: DEALS.length });
  const page = await apiRequest<WirePage<DealOut>>(`/deals?size=${FULL_PAGE}`);
  return mapPage(page, dealFromWire);
}

export async function getDeal(id: string): Promise<Deal> {
  if (USE_FIXTURES) {
    const deal = DEALS.find((d) => d.id === id);
    if (!deal) throw new Error("That deal could not be found.");
    return settle(deal);
  }
  const row = await apiRequest<DealOut>(`/deals/${encodeURIComponent(id)}`);
  return dealFromWire(row);
}

/* ------------------------------- companies ------------------------------- */
//
// A "company" here is a `customer` row in the customer service: `/customers` is the real path,
// `companyFromWire` the mapping. See `./wire.ts` and `./mappers.ts`.

export interface ListCompaniesParams {
  search?: string;
  status?: CompanyStatus | "all";
}

export async function listCompanies(params: ListCompaniesParams = {}): Promise<Page<Company>> {
  if (USE_FIXTURES) {
    const q = params.search?.trim().toLowerCase() ?? "";
    const filtered = COMPANIES.filter((c) => {
      const matchesQuery =
        q === "" ||
        c.name.toLowerCase().includes(q) ||
        c.industry.toLowerCase().includes(q) ||
        c.city.toLowerCase().includes(q);
      const matchesStatus = !params.status || params.status === "all" || c.status === params.status;
      return matchesQuery && matchesStatus;
    });
    return settle({ items: filtered, total: filtered.length });
  }
  const query = new URLSearchParams({ size: String(FULL_PAGE) });
  if (params.search?.trim()) query.set("q", params.search.trim());
  if (params.status && params.status !== "all") query.set("status", params.status);
  const page = await apiRequest<WirePage<CompanyOut>>(`/customers?${query.toString()}`);
  return mapPage(page, companyFromWire);
}

export async function getCompany(id: string): Promise<Company> {
  if (USE_FIXTURES) {
    const company = COMPANIES.find((c) => c.id === id);
    if (!company) throw new Error("That company could not be found.");
    return settle(company);
  }
  const row = await apiRequest<CompanyOut>(`/customers/${encodeURIComponent(id)}`);
  return companyFromWire(row);
}

/* -------------------------------- contacts ------------------------------- */

export interface ListContactsParams {
  search?: string;
  status?: ContactStatus | "all";
}

export async function listContacts(params: ListContactsParams = {}): Promise<Page<Contact>> {
  if (USE_FIXTURES) {
    const q = params.search?.trim().toLowerCase() ?? "";
    const filtered = CONTACTS.filter((c) => {
      const matchesQuery =
        q === "" ||
        c.full_name.toLowerCase().includes(q) ||
        c.title.toLowerCase().includes(q) ||
        c.email.toLowerCase().includes(q);
      const matchesStatus = !params.status || params.status === "all" || c.status === params.status;
      return matchesQuery && matchesStatus;
    });
    return settle({ items: filtered, total: filtered.length });
  }
  const query = new URLSearchParams({ size: String(FULL_PAGE) });
  if (params.search?.trim()) query.set("q", params.search.trim());
  if (params.status && params.status !== "all") query.set("status", params.status);
  const page = await apiRequest<WirePage<ContactOut>>(`/contacts?${query.toString()}`);
  return mapPage(page, contactFromWire);
}

export async function getContact(id: string): Promise<Contact> {
  if (USE_FIXTURES) {
    const contact = CONTACTS.find((c) => c.id === id);
    if (!contact) throw new Error("That contact could not be found.");
    return settle(contact);
  }
  const row = await apiRequest<ContactOut>(`/contacts/${encodeURIComponent(id)}`);
  return contactFromWire(row);
}

/* ------------------------------- activities ------------------------------ */
//
// The timeline lives with the pipeline, not the account book: the real path is
// `/deals/activities`, not `/activities`.

export interface ListActivitiesParams {
  dealId?: string;
  contactId?: string;
  companyId?: string;
  type?: ActivityType | "all";
}

function filterFixtureActivities(rows: Activity[], params: ListActivitiesParams): Activity[] {
  return rows
    .filter((a) => (params.dealId ? a.deal_id === params.dealId : true))
    .filter((a) => (params.contactId ? a.contact_id === params.contactId : true))
    .filter((a) => (params.companyId ? a.company_id === params.companyId : true))
    .filter((a) => (!params.type || params.type === "all" ? true : a.type === params.type))
    .sort((a, b) => Date.parse(b.occurred_at) - Date.parse(a.occurred_at));
}

export async function listActivities(params: ListActivitiesParams = {}): Promise<Activity[]> {
  if (USE_FIXTURES) return settle(filterFixtureActivities(ACTIVITIES, params));
  const query = new URLSearchParams({ size: String(FULL_PAGE) });
  if (params.dealId) query.set("deal_id", params.dealId);
  if (params.contactId) query.set("contact_id", params.contactId);
  if (params.companyId) query.set("company_id", params.companyId);
  if (params.type && params.type !== "all") query.set("type", params.type);
  // Already scope-filtered and newest-first from the server; no client-side re-filtering needed.
  const page = await apiRequest<WirePage<ActivityOut>>(`/deals/activities?${query.toString()}`);
  return page.items.map(activityFromWire);
}

/* --------------------------------- owners -------------------------------- */
//
// The rep directory is `GET /customers/owners` — reference data owned by the customer service,
// resolved by every `owner_id` in the product including deal-service's.

export async function getOwners(): Promise<Owner[]> {
  if (USE_FIXTURES) return settle(OWNERS);
  const rows = await apiRequest<OwnerOut[]>(`/customers/owners`);
  return rows.map(ownerFromWire);
}
