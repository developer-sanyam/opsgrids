/**
 * Typed CRM API client for OpsGrids Revenue.
 *
 * **Fixtures by default.** The CRM service is a skeleton on this branch, so the whole app renders
 * from `fixtures.ts` with no gateway and no login. Set `VITE_USE_FIXTURES=false` once the service
 * ships and every call switches to the gateway at the same-origin `/api/v1` prefix (Vite proxies
 * it to `http://localhost:8100` in dev) with the bearer token from the session — the live paths
 * are wired here already, so going live is a flag flip.
 */

import { apiRequest } from "@opsgrids/client";
import { ACTIVITIES, COMPANIES, CONTACTS, DEALS, OWNERS } from "./fixtures";
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

/** Default source. Fixtures unless `VITE_USE_FIXTURES=false` pins the app to the live gateway.
 *  Dev builds only: `import.meta.env.DEV` compiles to `false` in production, so a stray
 *  CI/production env var can never ship fixture mode — an app rendering fabricated data
 *  with the "Sample data" markers suppressed. */
export const USE_FIXTURES = import.meta.env.DEV && import.meta.env.VITE_USE_FIXTURES !== "false";

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
  return apiRequest<Page<Deal>>(`/deals?${query.toString()}`);
}

/** The whole deal book — powers the Overview KPIs, the funnel and the kanban board. */
export async function getAllDeals(): Promise<Page<Deal>> {
  if (USE_FIXTURES) return settle({ items: DEALS, total: DEALS.length });
  return apiRequest<Page<Deal>>(`/deals?size=${FULL_PAGE}`);
}

export async function getDeal(id: string): Promise<Deal> {
  if (USE_FIXTURES) {
    const deal = DEALS.find((d) => d.id === id);
    if (!deal) throw new Error("That deal could not be found.");
    return settle(deal);
  }
  return apiRequest<Deal>(`/deals/${id}`);
}

/* ------------------------------- companies ------------------------------- */

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
  return apiRequest<Page<Company>>(`/companies?${query.toString()}`);
}

export async function getCompany(id: string): Promise<Company> {
  if (USE_FIXTURES) {
    const company = COMPANIES.find((c) => c.id === id);
    if (!company) throw new Error("That company could not be found.");
    return settle(company);
  }
  return apiRequest<Company>(`/companies/${id}`);
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
  return apiRequest<Page<Contact>>(`/contacts?${query.toString()}`);
}

export async function getContact(id: string): Promise<Contact> {
  if (USE_FIXTURES) {
    const contact = CONTACTS.find((c) => c.id === id);
    if (!contact) throw new Error("That contact could not be found.");
    return settle(contact);
  }
  return apiRequest<Contact>(`/contacts/${id}`);
}

/* ------------------------------- activities ------------------------------ */

export interface ListActivitiesParams {
  dealId?: string;
  contactId?: string;
  companyId?: string;
  type?: ActivityType | "all";
}

export async function listActivities(params: ListActivitiesParams = {}): Promise<Activity[]> {
  const filter = (rows: Activity[]): Activity[] =>
    rows
      .filter((a) => (params.dealId ? a.deal_id === params.dealId : true))
      .filter((a) => (params.contactId ? a.contact_id === params.contactId : true))
      .filter((a) => (params.companyId ? a.company_id === params.companyId : true))
      .filter((a) => (!params.type || params.type === "all" ? true : a.type === params.type))
      .sort((a, b) => Date.parse(b.occurred_at) - Date.parse(a.occurred_at));

  if (USE_FIXTURES) return settle(filter(ACTIVITIES));
  const query = new URLSearchParams({ size: String(FULL_PAGE) });
  if (params.dealId) query.set("deal_id", params.dealId);
  if (params.contactId) query.set("contact_id", params.contactId);
  if (params.companyId) query.set("company_id", params.companyId);
  if (params.type && params.type !== "all") query.set("type", params.type);
  const page = await apiRequest<Page<Activity>>(`/activities?${query.toString()}`);
  return filter(page.items);
}

/* --------------------------------- owners -------------------------------- */

export async function getOwners(): Promise<Owner[]> {
  if (USE_FIXTURES) return settle(OWNERS);
  return apiRequest<Owner[]>(`/owners`);
}
