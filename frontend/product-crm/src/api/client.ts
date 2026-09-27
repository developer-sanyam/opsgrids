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
 * The write bodies (`*In` / `*Patch`) are the wire shapes verbatim — every one of them is
 * `extra="forbid"` server-side, so there is no divergence between this product's vocabulary and
 * the request body the way there is for a read; the `wire.ts` types are used directly as the
 * parameter types below rather than duplicated.
 *
 * Set `VITE_USE_FIXTURES=true` to render the whole app from `fixtures.ts` with no gateway and no
 * login — handy for offline design work. Fixture writes mutate the in-memory fixture arrays so
 * the screen behaves the same as live, and are gated on `import.meta.env.DEV` so they can never
 * ship in a production build.
 */

import { ApiError, apiRequest, revealRequest } from "@opsgrids/client";
import type { RevealHeaders } from "@opsgrids/client";
import {
  activityFromWire,
  companyFromWire,
  contactFromWire,
  dealFromWire,
  leadFromWire,
  mapPage,
  ownerFromWire,
  summaryFromWire,
} from "./mappers";
import { ACTIVITIES, COMPANIES, CONTACTS, DEALS, LEADS, OWNERS } from "./fixtures";
import type {
  ActivityIn,
  ActivityOut,
  CompanyIn,
  CompanyOut,
  CompanyPatch,
  ContactIn,
  ContactOut,
  ContactPatch,
  ContactPhoneIn,
  ContactPhoneRevealOut,
  DealIn,
  DealOut,
  DealPatch,
  LeadConvertOut,
  LeadIn,
  LeadOut,
  LeadPatch,
  LeadRevealIn,
  LeadRevealOut,
  OwnerIn,
  OwnerOut,
  OwnerPatch,
  StageMoveIn,
  SummaryOut,
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
  Lead,
  LeadConvertResult,
  LeadRevealField,
  LeadStatus,
  Owner,
  Page,
  Summary,
} from "./types";

/** Default source. Live unless `VITE_USE_FIXTURES=true` pins the app to fixtures.
 *  Dev builds only: `import.meta.env.DEV` compiles to `false` in production, so a stray
 *  CI/production env var can never ship fixture mode — an app rendering fabricated data
 *  with the "Sample data" markers suppressed. */
export const USE_FIXTURES = import.meta.env.DEV && import.meta.env.VITE_USE_FIXTURES === "true";

/** True when the app is talking to the real gateway. */
export const LIVE = !USE_FIXTURES;

/** A generous page size — one request returns the whole (small) book for this org. Used by
 *  screens that need the complete set (the directory, the kanban board, a record's own scoped
 *  activity feed) rather than a page of it. */
const FULL_PAGE = 200;

/** The real default page size for a paginated list screen (Companies, Contacts). */
const DEFAULT_PAGE_SIZE = 25;

/**
 * A fixture write refused the way the server refuses one: an `ApiError` carrying the status and
 * the problem document. Callers that read the status behave the same against fixtures as against
 * the gateway (an import reports a 409 as "skipped", not as a failure), and `problemMessage`
 * finds the sentence.
 */
function refusal(
  status: 404 | 409 | 422,
  title: string,
  detail: string,
  errors?: readonly { field: string; code: string; message: string }[],
): ApiError {
  return new ApiError(status, detail, { title, status, detail, ...(errors !== undefined ? { errors } : {}) });
}

const conflict = (detail: string): ApiError => refusal(409, "Conflict", detail);

/** Small artificial delay so loading states are exercised in fixtures mode. */
function settle<T>(value: T, ms = 240): Promise<T> {
  return new Promise((resolve) => setTimeout(() => resolve(value), ms));
}

/** Slice an in-memory fixture list into a `Page<T>` the same shape the gateway returns. */
function paginateFixture<T>(items: T[], page: number, size: number): Page<T> {
  const total = items.length;
  const pages = total === 0 ? 0 : Math.ceil(total / size);
  const start = (page - 1) * size;
  return { items: items.slice(start, start + size), total, page, size, pages };
}

/** A phone number with its country code, once spaces, hyphens, dots and brackets are stripped. */
const E164 = /^\+[1-9]\d{7,14}$/;

function fixtureId(prefix: string): string {
  return `${prefix}-${crypto.randomUUID()}`;
}

/* --------------------------------- deals --------------------------------- */

export interface ListDealsParams {
  search?: string;
  stage?: DealStage | "all";
  ownerId?: string | "all";
  companyId?: string;
  page?: number;
  size?: number;
  /** Aborts the request — the import/export dialog cancels an export or a lookup load. */
  signal?: AbortSignal | undefined;
}

export async function listDeals(params: ListDealsParams = {}): Promise<Page<Deal>> {
  const page = params.page ?? 1;
  const size = params.size ?? DEFAULT_PAGE_SIZE;
  if (USE_FIXTURES) {
    const q = params.search?.trim().toLowerCase() ?? "";
    const filtered = DEALS.filter((d) => {
      const matchesQuery = q === "" || d.name.toLowerCase().includes(q);
      const matchesStage = !params.stage || params.stage === "all" || d.stage === params.stage;
      const matchesOwner =
        !params.ownerId || params.ownerId === "all" || d.owner_id === params.ownerId;
      const matchesCompany = !params.companyId || d.company_id === params.companyId;
      return matchesQuery && matchesStage && matchesOwner && matchesCompany;
    });
    return settle(paginateFixture(filtered, page, size));
  }
  const query = new URLSearchParams({ page: String(page), size: String(size) });
  if (params.search?.trim()) query.set("q", params.search.trim());
  if (params.stage && params.stage !== "all") query.set("stage", params.stage);
  if (params.ownerId && params.ownerId !== "all") query.set("owner_id", params.ownerId);
  if (params.companyId) query.set("company_id", params.companyId);
  const result = await apiRequest<WirePage<DealOut>>(`/deals?${query.toString()}`, {
    signal: params.signal,
  });
  return mapPage(result, dealFromWire);
}

export interface GetAllDealsParams {
  /** Narrow to one rep's own deals — the Overview's "My pipeline" toggle. */
  ownerId?: string;
}

/** The whole deal book — powers the Overview's "needs attention" scan, "won this month" and the
 *  kanban board, none of which can be usefully paginated: a board needs every column populated at
 *  once. The headline pipeline figures (value, open count, win rate) come from `getSummary()`
 *  instead, the server's own rollup — never a count over this capped page. */
export async function getAllDeals(params: GetAllDealsParams = {}): Promise<Page<Deal>> {
  if (USE_FIXTURES) {
    const filtered = params.ownerId ? DEALS.filter((d) => d.owner_id === params.ownerId) : DEALS;
    return settle(paginateFixture(filtered, 1, FULL_PAGE));
  }
  const query = new URLSearchParams({ size: String(FULL_PAGE) });
  if (params.ownerId) query.set("owner_id", params.ownerId);
  const result = await apiRequest<WirePage<DealOut>>(`/deals?${query.toString()}`);
  return mapPage(result, dealFromWire);
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

/** `POST /deals` — `crm.pipeline.deal.write`. */
export async function createDeal(input: DealIn): Promise<Deal> {
  if (USE_FIXTURES) {
    // The server compares deal names exactly, capitals included.
    if (DEALS.some((d) => d.name === input.name)) {
      throw conflict("A deal with that name already exists");
    }
    const deal: Deal = {
      id: fixtureId("deal"),
      name: input.name,
      company_id: input.company_id,
      primary_contact_id: input.primary_contact_id ?? null,
      value_minor: input.value_minor,
      currency: input.currency ?? "INR",
      stage: input.stage ?? "lead",
      owner_id: input.owner_id,
      probability: input.probability ?? 20,
      close_date: input.close_date,
      created_at: new Date().toISOString(),
      last_activity_at: null,
      source: input.source ?? "",
    };
    DEALS.unshift(deal);
    return settle(deal);
  }
  const row = await apiRequest<DealOut>("/deals", { method: "POST", body: input });
  return dealFromWire(row);
}

/** `PATCH /deals/{id}` — `crm.pipeline.deal.write`. No `stage`: see `moveDealStage`. */
export async function updateDeal(id: string, patch: DealPatch): Promise<Deal> {
  if (USE_FIXTURES) {
    const index = DEALS.findIndex((d) => d.id === id);
    if (index === -1) throw new Error("That deal could not be found.");
    const current = DEALS[index];
    if (!current) throw new Error("That deal could not be found.");
    const updated: Deal = {
      ...current,
      ...(patch.name !== undefined ? { name: patch.name } : {}),
      ...(patch.company_id !== undefined ? { company_id: patch.company_id } : {}),
      ...(patch.primary_contact_id !== undefined
        ? { primary_contact_id: patch.primary_contact_id }
        : {}),
      ...(patch.owner_id !== undefined ? { owner_id: patch.owner_id } : {}),
      ...(patch.value_minor !== undefined ? { value_minor: patch.value_minor } : {}),
      ...(patch.currency !== undefined ? { currency: patch.currency } : {}),
      ...(patch.close_date !== undefined ? { close_date: patch.close_date } : {}),
      ...(patch.probability !== undefined && patch.probability !== null
        ? { probability: patch.probability }
        : {}),
      ...(patch.source !== undefined ? { source: patch.source } : {}),
    };
    DEALS[index] = updated;
    return settle(updated);
  }
  const row = await apiRequest<DealOut>(`/deals/${encodeURIComponent(id)}`, {
    method: "PATCH",
    body: patch,
  });
  return dealFromWire(row);
}

/** `DELETE /deals/{id}` — `crm.pipeline.deal.delete`. Soft delete server-side. */
export async function deleteDeal(id: string): Promise<void> {
  if (USE_FIXTURES) {
    const index = DEALS.findIndex((d) => d.id === id);
    if (index === -1) throw new Error("That deal could not be found.");
    DEALS.splice(index, 1);
    return settle(undefined);
  }
  await apiRequest<undefined>(`/deals/${encodeURIComponent(id)}`, { method: "DELETE" });
}

/** `POST /deals/{id}/stage` — `crm.pipeline.deal.write`. 409 on a closed deal or a no-op move. */
export async function moveDealStage(id: string, input: StageMoveIn): Promise<Deal> {
  if (USE_FIXTURES) {
    const index = DEALS.findIndex((d) => d.id === id);
    if (index === -1) throw new Error("That deal could not be found.");
    const current = DEALS[index];
    if (!current) throw new Error("That deal could not be found.");
    if (current.stage === "closed_won" || current.stage === "closed_lost") {
      throw new Error("A closed deal can't be moved.");
    }
    if (current.stage === input.stage) {
      throw new Error("That deal is already in this stage.");
    }
    const updated: Deal = {
      ...current,
      stage: input.stage,
      ...(input.probability !== undefined && input.probability !== null
        ? { probability: input.probability }
        : {}),
    };
    DEALS[index] = updated;
    return settle(updated);
  }
  const row = await apiRequest<DealOut>(`/deals/${encodeURIComponent(id)}/stage`, {
    method: "POST",
    body: input,
  });
  return dealFromWire(row);
}

export interface GetSummaryParams {
  /** Narrow the rollup to one rep's own pipeline. */
  ownerId?: string;
  /** `"me"` narrows to the caller's own pipeline, resolved server-side from the token. */
  owner?: "me";
}

/** `GET /deals/summary` — the server's own pipeline rollup, scope-aware. `owner`/`ownerId` power
 *  the Overview's "My pipeline" toggle without composing an owner filter over `getAllDeals()`'s
 *  capped page — this is the real total, not a count over 200 rows. */
export async function getSummary(params: GetSummaryParams = {}): Promise<Summary> {
  if (USE_FIXTURES) {
    // Fixture mode mints no real token, so "me" resolves to the fixture identity's own rep
    // record — the first owner, Sofia Marchetti — the same stand-in `AuthProvider.tsx` uses.
    const ownerId = params.owner === "me" ? OWNERS[0]?.id : params.ownerId;
    const scoped = ownerId ? DEALS.filter((d) => d.owner_id === ownerId) : DEALS;
    const scopedActivities = ownerId ? ACTIVITIES.filter((a) => a.owner_id === ownerId) : ACTIVITIES;
    const openDeals = scoped.filter((d) => d.stage !== "closed_won" && d.stage !== "closed_lost");
    const won = scoped.filter((d) => d.stage === "closed_won");
    const lost = scoped.filter((d) => d.stage === "closed_lost");
    const decided = won.length + lost.length;
    const weekAgo = Date.now() - 7 * 86_400_000;
    return settle({
      pipeline_value_minor: openDeals.reduce((s, d) => s + d.value_minor, 0),
      weighted_pipeline_minor: openDeals.reduce(
        (s, d) => s + Math.round((d.value_minor * d.probability) / 100),
        0,
      ),
      open_deals: openDeals.length,
      win_rate_pct: decided === 0 ? 0 : Math.round((won.length / decided) * 100),
      won_value_minor: won.reduce((s, d) => s + d.value_minor, 0),
      activities_this_week: scopedActivities.filter((a) => Date.parse(a.occurred_at) >= weekAgo)
        .length,
      currency: scoped[0]?.currency ?? "INR",
      trend: null,
      funnel: [],
    });
  }
  const query = new URLSearchParams();
  if (params.owner === "me") query.set("owner", "me");
  else if (params.ownerId) query.set("owner_id", params.ownerId);
  const qs = query.toString();
  const row = await apiRequest<SummaryOut>(`/deals/summary${qs ? `?${qs}` : ""}`);
  return summaryFromWire(row);
}

/* ------------------------------- companies ------------------------------- */
//
// A "company" here is a `customer` row in the customer service: `/customers` is the real path,
// `companyFromWire` the mapping. See `./wire.ts` and `./mappers.ts`.

export interface ListCompaniesParams {
  search?: string;
  status?: CompanyStatus | "all";
  page?: number;
  size?: number;
  signal?: AbortSignal | undefined;
}

export async function listCompanies(params: ListCompaniesParams = {}): Promise<Page<Company>> {
  const page = params.page ?? 1;
  const size = params.size ?? DEFAULT_PAGE_SIZE;
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
    return settle(paginateFixture(filtered, page, size));
  }
  const query = new URLSearchParams({ page: String(page), size: String(size) });
  if (params.search?.trim()) query.set("q", params.search.trim());
  if (params.status && params.status !== "all") query.set("status", params.status);
  const result = await apiRequest<WirePage<CompanyOut>>(`/customers?${query.toString()}`, {
    signal: params.signal,
  });
  return mapPage(result, companyFromWire);
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

/** `POST /customers` — `crm.book.company.write`. 409 on a duplicate name within the org. */
export async function createCompany(input: CompanyIn): Promise<Company> {
  if (USE_FIXTURES) {
    // The server compares company names ignoring case and surrounding spaces.
    if (COMPANIES.some((c) => c.name.trim().toLowerCase() === input.name.trim().toLowerCase())) {
      throw conflict("A company with that name already exists");
    }
    const company: Company = {
      id: fixtureId("comp"),
      name: input.name,
      domain: input.domain ?? "",
      industry: input.industry ?? "",
      size_band: input.size_band ?? "",
      city: input.city ?? "",
      country: input.country ?? "",
      status: input.status ?? "prospect",
      owner_id: input.owner_id,
      annual_revenue_minor: input.annual_revenue_minor ?? 0,
      currency: input.currency ?? "INR",
      created_at: new Date().toISOString(),
    };
    COMPANIES.unshift(company);
    return settle(company);
  }
  const row = await apiRequest<CompanyOut>("/customers", { method: "POST", body: input });
  return companyFromWire(row);
}

/** `PATCH /customers/{id}` — `crm.book.company.write`. 409 on renaming onto an existing name. */
export async function updateCompany(id: string, patch: CompanyPatch): Promise<Company> {
  if (USE_FIXTURES) {
    const index = COMPANIES.findIndex((c) => c.id === id);
    if (index === -1) throw new Error("That company could not be found.");
    const current = COMPANIES[index];
    if (!current) throw new Error("That company could not be found.");
    if (
      patch.name !== undefined &&
      COMPANIES.some((c) => c.id !== id && c.name.toLowerCase() === patch.name?.toLowerCase())
    ) {
      throw new Error("A company with that name already exists.");
    }
    const updated: Company = {
      ...current,
      ...(patch.name !== undefined ? { name: patch.name } : {}),
      ...(patch.owner_id !== undefined ? { owner_id: patch.owner_id } : {}),
      ...(patch.domain !== undefined ? { domain: patch.domain } : {}),
      ...(patch.industry !== undefined ? { industry: patch.industry } : {}),
      ...(patch.size_band !== undefined ? { size_band: patch.size_band } : {}),
      ...(patch.city !== undefined ? { city: patch.city } : {}),
      ...(patch.country !== undefined ? { country: patch.country } : {}),
      ...(patch.status !== undefined ? { status: patch.status } : {}),
      ...(patch.annual_revenue_minor !== undefined
        ? { annual_revenue_minor: patch.annual_revenue_minor }
        : {}),
      ...(patch.currency !== undefined ? { currency: patch.currency } : {}),
    };
    COMPANIES[index] = updated;
    return settle(updated);
  }
  const row = await apiRequest<CompanyOut>(`/customers/${encodeURIComponent(id)}`, {
    method: "PATCH",
    body: patch,
  });
  return companyFromWire(row);
}

/** `DELETE /customers/{id}` — `crm.book.company.delete`. 409 while the company still has
 *  contacts — the server's explanation is surfaced verbatim by the caller. */
export async function deleteCompany(id: string): Promise<void> {
  if (USE_FIXTURES) {
    const index = COMPANIES.findIndex((c) => c.id === id);
    if (index === -1) throw new Error("That company could not be found.");
    if (CONTACTS.some((c) => c.company_id === id)) {
      throw new Error("This company still has contacts. Remove them before deleting it.");
    }
    COMPANIES.splice(index, 1);
    return settle(undefined);
  }
  await apiRequest<undefined>(`/customers/${encodeURIComponent(id)}`, { method: "DELETE" });
}

/* -------------------------------- contacts ------------------------------- */

export interface ListContactsParams {
  search?: string;
  status?: ContactStatus | "all";
  companyId?: string;
  page?: number;
  size?: number;
  signal?: AbortSignal | undefined;
}

export async function listContacts(params: ListContactsParams = {}): Promise<Page<Contact>> {
  const page = params.page ?? 1;
  const size = params.size ?? DEFAULT_PAGE_SIZE;
  if (USE_FIXTURES) {
    const q = params.search?.trim().toLowerCase() ?? "";
    const filtered = CONTACTS.filter((c) => {
      const matchesQuery =
        q === "" ||
        c.full_name.toLowerCase().includes(q) ||
        c.title.toLowerCase().includes(q) ||
        c.email.toLowerCase().includes(q);
      const matchesStatus = !params.status || params.status === "all" || c.status === params.status;
      const matchesCompany = !params.companyId || c.company_id === params.companyId;
      return matchesQuery && matchesStatus && matchesCompany;
    });
    return settle(paginateFixture(filtered, page, size));
  }
  const query = new URLSearchParams({ page: String(page), size: String(size) });
  if (params.search?.trim()) query.set("q", params.search.trim());
  if (params.status && params.status !== "all") query.set("status", params.status);
  if (params.companyId) query.set("company_id", params.companyId);
  const result = await apiRequest<WirePage<ContactOut>>(`/contacts?${query.toString()}`, {
    signal: params.signal,
  });
  return mapPage(result, contactFromWire);
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

/** Derive a display mask the same shape the service returns, for fixture-mode creates/updates —
 *  never the raw number, so fixture mode cannot accidentally demonstrate a real one either. */
function maskPhone(rawPhone: string): string {
  const digits = rawPhone.replace(/[^\d]/g, "");
  const last = digits.slice(-3);
  return `+•• •••• ••${last}`;
}

/** `POST /contacts` — `crm.book.contact.write`. No `phone` field: see `ContactIn`'s doc comment. */
export async function createContact(input: ContactIn): Promise<Contact> {
  if (USE_FIXTURES) {
    // The server compares contact emails exactly, capitals included.
    if (CONTACTS.some((c) => c.email === input.email)) {
      throw conflict("A contact with that email already exists");
    }
    if (!COMPANIES.some((c) => c.id === input.company_id)) {
      throw refusal(404, "Not found", "That company is not part of this organization");
    }
    if (input.raw_phone !== undefined && !E164.test(input.raw_phone.replace(/[\s\-().]/g, ""))) {
      throw refusal(422, "Validation failed", "Request validation failed", [
        { field: "raw_phone", code: "invalid_phone", message: "That is not a valid phone number with a country code" },
      ]);
    }
    const contact: Contact = {
      id: fixtureId("con"),
      full_name: input.full_name,
      title: input.title ?? "",
      email: input.email,
      phone: input.raw_phone ? maskPhone(input.raw_phone) : "—",
      company_id: input.company_id,
      status: input.status ?? "lead",
      owner_id: input.owner_id,
      last_activity_at: null,
      created_at: new Date().toISOString(),
    };
    CONTACTS.unshift(contact);
    return settle(contact);
  }
  const row = await apiRequest<ContactOut>("/contacts", { method: "POST", body: input });
  return contactFromWire(row);
}

/** `PATCH /contacts/{id}` — `crm.book.contact.write`. No `phone`/`raw_phone`: use
 *  `setContactPhone`. */
export async function updateContact(id: string, patch: ContactPatch): Promise<Contact> {
  if (USE_FIXTURES) {
    const index = CONTACTS.findIndex((c) => c.id === id);
    if (index === -1) throw new Error("That contact could not be found.");
    const current = CONTACTS[index];
    if (!current) throw new Error("That contact could not be found.");
    if (
      patch.email !== undefined &&
      CONTACTS.some((c) => c.id !== id && c.email.toLowerCase() === patch.email?.toLowerCase())
    ) {
      throw new Error("A contact with that email already exists.");
    }
    const updated: Contact = {
      ...current,
      ...(patch.full_name !== undefined ? { full_name: patch.full_name } : {}),
      ...(patch.title !== undefined ? { title: patch.title } : {}),
      ...(patch.email !== undefined ? { email: patch.email } : {}),
      ...(patch.company_id !== undefined ? { company_id: patch.company_id } : {}),
      ...(patch.owner_id !== undefined ? { owner_id: patch.owner_id } : {}),
      ...(patch.status !== undefined ? { status: patch.status } : {}),
    };
    CONTACTS[index] = updated;
    return settle(updated);
  }
  const row = await apiRequest<ContactOut>(`/contacts/${encodeURIComponent(id)}`, {
    method: "PATCH",
    body: patch,
  });
  return contactFromWire(row);
}

/** `DELETE /contacts/{id}` — `crm.book.contact.delete`. */
export async function deleteContact(id: string): Promise<void> {
  if (USE_FIXTURES) {
    const index = CONTACTS.findIndex((c) => c.id === id);
    if (index === -1) throw new Error("That contact could not be found.");
    CONTACTS.splice(index, 1);
    return settle(undefined);
  }
  await apiRequest<undefined>(`/contacts/${encodeURIComponent(id)}`, { method: "DELETE" });
}

/** `PUT /contacts/{id}/phone` — `crm.book.contact.write`. The only route a raw number reaches the
 *  service through; the response is the mask, never a read-back of what was just typed. */
export async function setContactPhone(id: string, input: ContactPhoneIn): Promise<Contact> {
  if (USE_FIXTURES) {
    const index = CONTACTS.findIndex((c) => c.id === id);
    if (index === -1) throw new Error("That contact could not be found.");
    const current = CONTACTS[index];
    if (!current) throw new Error("That contact could not be found.");
    if (!/^\+\d{1,3}\d{4,}$/.test(input.raw_phone.replace(/\s/g, ""))) {
      throw new Error("Enter the number with a country code, like +91 98765 43210.");
    }
    const updated: Contact = { ...current, phone: maskPhone(input.raw_phone) };
    CONTACTS[index] = updated;
    return settle(updated);
  }
  const row = await apiRequest<ContactOut>(`/contacts/${encodeURIComponent(id)}/phone`, {
    method: "PUT",
    body: input,
  });
  return contactFromWire(row);
}

/** A plausible number for a fixture mask: every `•` is filled with a fixed digit, so a contact
 *  always reveals the same number. A mask with no `•` in it ("—") stands for no number at all. */
function unmaskFixturePhone(mask: string): string | null {
  if (!mask.includes("•")) return null;
  const filler = "7654321";
  let next = 0;
  return mask.replace(/•/g, () => filler.charAt(next++ % filler.length));
}

/**
 * `POST /contacts/{id}/phone:reveal` — `crm.book.contact.read_sensitive` and a second factor. The
 * one call that returns the real number, audited server-side on every read; `null` when the
 * contact has no sealed number. `headers` carry the step-up token as the call's only credential
 * (`useStepUp().run` hands them over) — see `revealRequest`. Fixture mode reads no header: there
 * is no auth service to step up against.
 */
export async function revealContactPhone(
  id: string,
  headers: RevealHeaders,
): Promise<string | null> {
  if (USE_FIXTURES) {
    const contact = CONTACTS.find((c) => c.id === id);
    if (!contact) throw refusal(404, "Not found", "That contact is not part of this organization");
    return settle(unmaskFixturePhone(contact.phone));
  }
  const row = await apiRequest<ContactPhoneRevealOut>(
    `/contacts/${encodeURIComponent(id)}/phone:reveal`,
    { method: "POST", ...revealRequest(headers) },
  );
  return row.phone;
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
  page?: number;
  size?: number;
}

function filterFixtureActivities(rows: Activity[], params: ListActivitiesParams): Activity[] {
  return rows
    .filter((a) => (params.dealId ? a.deal_id === params.dealId : true))
    .filter((a) => (params.contactId ? a.contact_id === params.contactId : true))
    .filter((a) => (params.companyId ? a.company_id === params.companyId : true))
    .filter((a) => (!params.type || params.type === "all" ? true : a.type === params.type))
    .sort((a, b) => Date.parse(b.occurred_at) - Date.parse(a.occurred_at));
}

/**
 * The activity timeline. Scoped views (a deal's, a company's) pass a generous `size` to get the
 * whole (small) history in one request; the standalone Activities screen pages through it with
 * `LoadMore`.
 */
export async function listActivities(params: ListActivitiesParams = {}): Promise<Page<Activity>> {
  const page = params.page ?? 1;
  const size = params.size ?? FULL_PAGE;
  if (USE_FIXTURES) {
    const filtered = filterFixtureActivities(ACTIVITIES, params);
    return settle(paginateFixture(filtered, page, size));
  }
  const query = new URLSearchParams({ page: String(page), size: String(size) });
  if (params.dealId) query.set("deal_id", params.dealId);
  if (params.contactId) query.set("contact_id", params.contactId);
  if (params.companyId) query.set("company_id", params.companyId);
  if (params.type && params.type !== "all") query.set("type", params.type);
  // Already scope-filtered and newest-first from the server; no client-side re-filtering needed.
  const result = await apiRequest<WirePage<ActivityOut>>(`/deals/activities?${query.toString()}`);
  return mapPage(result, activityFromWire);
}

/** `POST /deals/activities` — `crm.pipeline.activity.write`. Append-only: no edit, no delete. */
export async function logActivity(input: ActivityIn): Promise<Activity> {
  if (USE_FIXTURES) {
    if (!input.deal_id && !input.company_id && !input.contact_id) {
      throw new Error("Name at least one of a deal, a company or a contact.");
    }
    const occurredAt = input.occurred_at ?? new Date().toISOString();
    if (Date.parse(occurredAt) > Date.now()) {
      throw new Error("occurred_at cannot be in the future.");
    }
    const activity: Activity = {
      id: fixtureId("act"),
      type: input.type,
      subject: input.subject,
      body: input.body ?? null,
      deal_id: input.deal_id ?? null,
      company_id: input.company_id ?? null,
      contact_id: input.contact_id ?? null,
      owner_id: input.owner_id ?? OWNERS[0]?.id ?? "",
      occurred_at: occurredAt,
      direction: input.direction ?? null,
    };
    ACTIVITIES.unshift(activity);
    if (input.deal_id) {
      const dealIndex = DEALS.findIndex((d) => d.id === input.deal_id);
      const deal = dealIndex === -1 ? undefined : DEALS[dealIndex];
      if (deal) DEALS[dealIndex] = { ...deal, last_activity_at: occurredAt };
    }
    if (input.contact_id) {
      const contactIndex = CONTACTS.findIndex((c) => c.id === input.contact_id);
      const contact = contactIndex === -1 ? undefined : CONTACTS[contactIndex];
      if (contact) CONTACTS[contactIndex] = { ...contact, last_activity_at: occurredAt };
    }
    return settle(activity);
  }
  const row = await apiRequest<ActivityOut>("/deals/activities", { method: "POST", body: input });
  return activityFromWire(row);
}

/* --------------------------------- owners -------------------------------- */
//
// The rep directory is `GET /customers/owners` — reference data owned by the customer service,
// resolved by every `owner_id` in the product including deal-service's.

export async function getOwners(signal?: AbortSignal): Promise<Owner[]> {
  if (USE_FIXTURES) return settle(OWNERS);
  const rows = await apiRequest<OwnerOut[]>(`/customers/owners`, { signal });
  return rows.map(ownerFromWire);
}

/** `POST /customers/owners` — `crm.book.owner.write`. The prerequisite for every other create: a
 *  production org starts with zero owners. */
export async function createOwner(input: OwnerIn): Promise<Owner> {
  if (USE_FIXTURES) {
    if (OWNERS.some((o) => o.email.toLowerCase() === input.email.toLowerCase())) {
      throw conflict("An owner with that email already exists");
    }
    const owner: Owner = {
      id: fixtureId("own"),
      full_name: input.full_name,
      email: input.email,
      role: input.role ?? "",
      is_active: true,
    };
    OWNERS.push(owner);
    return settle(owner);
  }
  const row = await apiRequest<OwnerOut>("/customers/owners", { method: "POST", body: input });
  return ownerFromWire(row);
}

/** `PATCH /customers/owners/{id}` — `crm.book.owner.write` (ORG scope). Edits a rep's plain
 *  fields, and — via `is_active` — deactivates or reactivates them. Deactivating refuses (409)
 *  while the rep still owns a live company or contact; reassign those first. */
export async function updateOwner(id: string, patch: OwnerPatch): Promise<Owner> {
  if (USE_FIXTURES) {
    const index = OWNERS.findIndex((o) => o.id === id);
    if (index === -1) throw new Error("That team member could not be found.");
    const current = OWNERS[index];
    if (!current) throw new Error("That team member could not be found.");
    if (
      patch.email !== undefined &&
      OWNERS.some((o) => o.id !== id && o.email.toLowerCase() === patch.email?.toLowerCase())
    ) {
      throw new Error("A team member with that email already exists.");
    }
    const owner: Owner = {
      ...current,
      ...(patch.full_name !== undefined ? { full_name: patch.full_name } : {}),
      ...(patch.email !== undefined ? { email: patch.email } : {}),
      ...(patch.role !== undefined ? { role: patch.role } : {}),
      ...(patch.is_active !== undefined ? { is_active: patch.is_active } : {}),
    };
    OWNERS[index] = owner;
    return settle(owner);
  }
  const row = await apiRequest<OwnerOut>(`/customers/owners/${encodeURIComponent(id)}`, {
    method: "PATCH",
    body: patch,
  });
  return ownerFromWire(row);
}

/* ---------------------------------- leads --------------------------------- */
//
// `services/lead` mounted at `/api/v1/leads` — the intake ahead of the pipeline. A lead's
// `email` / `phone` are always the masked display form on this client; the raw value is sealed
// server-side and never returned. `converted_*` are set together, once, by `convertLead`.

export interface ListLeadsParams {
  status?: LeadStatus | "all";
  ownerId?: string | "all";
  page?: number;
  size?: number;
  signal?: AbortSignal | undefined;
}

export async function listLeads(params: ListLeadsParams = {}): Promise<Page<Lead>> {
  const page = params.page ?? 1;
  const size = params.size ?? DEFAULT_PAGE_SIZE;
  if (USE_FIXTURES) {
    const filtered = LEADS.filter((l) => {
      const matchesStatus = !params.status || params.status === "all" || l.status === params.status;
      const matchesOwner =
        !params.ownerId || params.ownerId === "all" || l.owner_id === params.ownerId;
      return matchesStatus && matchesOwner;
    });
    return settle(paginateFixture(filtered, page, size));
  }
  const query = new URLSearchParams({ page: String(page), size: String(size) });
  if (params.status && params.status !== "all") query.set("status", params.status);
  if (params.ownerId && params.ownerId !== "all") query.set("owner_id", params.ownerId);
  const result = await apiRequest<WirePage<LeadOut>>(`/leads?${query.toString()}`, {
    signal: params.signal,
  });
  return mapPage(result, leadFromWire);
}

export async function getLead(id: string): Promise<Lead> {
  if (USE_FIXTURES) {
    const lead = LEADS.find((l) => l.id === id);
    if (!lead) throw new Error("That lead could not be found.");
    return settle(lead);
  }
  const row = await apiRequest<LeadOut>(`/leads/${encodeURIComponent(id)}`);
  return leadFromWire(row);
}

/** `POST /leads` — `crm.lead.lead.write`. `owner_id` omitted assigns from the organization's
 *  round-robin — fixtures mode leaves it unassigned, since there is no rule to simulate. */
export async function createLead(input: LeadIn): Promise<Lead> {
  if (USE_FIXTURES) {
    // The server compares lead emails exactly, capitals included.
    if (input.email !== undefined && input.email !== null && LEADS.some((l) => l.email === input.email)) {
      throw conflict("A lead with that email already exists");
    }
    if (input.phone !== undefined && input.phone !== null && !E164.test(input.phone.replace(/[\s\-().]/g, ""))) {
      throw refusal(422, "Validation failed", "Request validation failed", [
        { field: "phone", code: "invalid_phone", message: "That is not a valid phone number with a country code" },
      ]);
    }
    const lead: Lead = {
      id: fixtureId("lead"),
      full_name: input.full_name,
      company: input.company ?? null,
      email: input.email ?? null,
      phone: input.phone ?? null,
      source: input.source ?? "manual",
      status: "new",
      owner_id: input.owner_id ?? null,
      notes: input.notes ?? "",
      converted_company_id: null,
      converted_contact_id: null,
      converted_deal_id: null,
      converted_at: null,
      created_at: new Date().toISOString(),
    };
    LEADS.unshift(lead);
    return settle(lead);
  }
  const row = await apiRequest<LeadOut>("/leads", { method: "POST", body: input });
  return leadFromWire(row);
}

/** `PATCH /leads/{id}` — `crm.lead.lead.write`. No path to `status: "converted"` here — see
 *  `convertLead`. */
export async function updateLead(id: string, patch: LeadPatch): Promise<Lead> {
  if (USE_FIXTURES) {
    const index = LEADS.findIndex((l) => l.id === id);
    if (index === -1) throw new Error("That lead could not be found.");
    const current = LEADS[index];
    if (!current) throw new Error("That lead could not be found.");
    const lead: Lead = {
      ...current,
      ...(patch.full_name !== undefined ? { full_name: patch.full_name } : {}),
      ...(patch.company !== undefined ? { company: patch.company } : {}),
      ...(patch.status !== undefined ? { status: patch.status } : {}),
      ...(patch.owner_id !== undefined ? { owner_id: patch.owner_id } : {}),
      ...(patch.notes !== undefined ? { notes: patch.notes } : {}),
    };
    LEADS[index] = lead;
    return settle(lead);
  }
  const row = await apiRequest<LeadOut>(`/leads/${encodeURIComponent(id)}`, {
    method: "PATCH",
    body: patch,
  });
  return leadFromWire(row);
}

/** `POST /leads/{id}:convert` — `crm.lead.lead.convert`. Idempotent: converting an
 *  already-converted lead returns the same ids rather than erroring. Fixtures mode fabricates
 *  ids on first conversion and returns the same ones on a repeat call, the same guarantee the
 *  live service makes. */
export async function convertLead(id: string): Promise<LeadConvertResult> {
  if (USE_FIXTURES) {
    const index = LEADS.findIndex((l) => l.id === id);
    if (index === -1) throw new Error("That lead could not be found.");
    const current = LEADS[index];
    if (!current) throw new Error("That lead could not be found.");
    if (current.status === "converted" && current.converted_company_id && current.converted_deal_id) {
      return settle({
        lead_id: current.id,
        company_id: current.converted_company_id,
        contact_id: current.converted_contact_id,
        deal_id: current.converted_deal_id,
      });
    }
    if (current.status !== "qualified") {
      throw new Error(`A lead in "${current.status}" cannot be converted — qualify it first.`);
    }
    const companyId = fixtureId("comp");
    const contactId = current.email ? fixtureId("con") : null;
    const dealId = fixtureId("deal");
    const converted: Lead = {
      ...current,
      status: "converted",
      converted_company_id: companyId,
      converted_contact_id: contactId,
      converted_deal_id: dealId,
      converted_at: new Date().toISOString(),
    };
    LEADS[index] = converted;
    return settle({ lead_id: current.id, company_id: companyId, contact_id: contactId, deal_id: dealId });
  }
  const row = await apiRequest<LeadConvertOut>(`/leads/${encodeURIComponent(id)}:convert`, {
    method: "POST",
  });
  return {
    lead_id: row.lead_id,
    company_id: row.company_id,
    contact_id: row.contact_id,
    deal_id: row.deal_id,
  };
}

/** The value a fixture lead reveals. A lead created in this session holds what was typed; a
 *  seeded one holds a mask, and gets a plausible value of its own. */
function fixtureLeadValue(lead: Lead, field: LeadRevealField): string | null {
  const stored = lead[field];
  if (stored === null) return null;
  if (!stored.includes("•")) return stored;
  if (field === "phone") return `+91 98765 0${stored.slice(-4)}`;
  const local = lead.full_name
    .toLowerCase()
    .replace(/[^a-z]+/g, ".")
    .replace(/^\.+|\.+$/g, "");
  const host = lead.company?.toLowerCase().replace(/[^a-z0-9]+/g, "") || "mail";
  return `${local}@${host}.example`;
}

/**
 * `POST /leads/{id}:reveal` — `crm.lead.lead.read_sensitive` and a second factor. Returns the real
 * phone number or email address of one lead, audited server-side on every read. A lead with
 * nothing sealed in that field is a 404 whose sentence says so. `headers` carry the step-up token
 * as the call's only credential (`useStepUp().run` hands them over) — see `revealRequest`.
 * Fixture mode reads no header: there is no auth service to step up against.
 */
export async function revealLeadField(
  id: string,
  field: LeadRevealField,
  headers: RevealHeaders,
): Promise<string> {
  if (USE_FIXTURES) {
    const lead = LEADS.find((l) => l.id === id);
    if (!lead) throw refusal(404, "Not found", "That lead is not part of this organization");
    const value = fixtureLeadValue(lead, field);
    if (value === null) {
      const what = field === "phone" ? "phone number" : "email address";
      throw refusal(404, "Not found", `No ${what} is on file for this lead`);
    }
    return settle(value);
  }
  const body: LeadRevealIn = { field };
  const row = await apiRequest<LeadRevealOut>(`/leads/${encodeURIComponent(id)}:reveal`, {
    method: "POST",
    body,
    ...revealRequest(headers),
  });
  return row.value;
}
