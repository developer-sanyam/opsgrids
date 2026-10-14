/**
 * The merchant's enquiry inbox — `/storefront/enquiries`. Fixtures by default in dev, as
 * everywhere else in this app.
 */

import { ApiError, apiRequest, paginateFixture, settle } from "@opsgrids/client";

import { USE_FIXTURES } from "@/api/client";
import { ENQUIRY_TRANSITIONS } from "./types";
import type { EnquiryOut, EnquiryStatus, ListEnquiriesParams, PagedResult } from "./types";

const SEED: EnquiryOut[] = [
  {
    id: "enq-fixture-1",
    name: "Rajeev Bhatia",
    company: "Sunrise Engineering",
    email_masked: "ra••••@sunrise.example.in",
    phone_masked: "••••••1180",
    gstin: "27AABCU9603R1ZX",
    requirement: "200 coils of 2.5 sq mm FRLS cable, delivered to our Bhiwandi site.",
    quantity: "200 coils",
    lines: [],
    status: "new",
    created_at: "2026-09-15T09:12:00Z",
    updated_at: "2026-09-15T09:12:00Z",
  },
  {
    id: "enq-fixture-2",
    name: "Meenakshi Rao",
    company: "Deccan Infra",
    email_masked: "me••••@deccaninfra.example.in",
    phone_masked: "••••••4402",
    gstin: null,
    requirement: "Quote against drawing DX-118 for M12 hex bolts, grade 8.8, quantity 5000.",
    quantity: "5000 pcs",
    lines: [],
    status: "contacted",
    created_at: "2026-09-12T14:00:00Z",
    updated_at: "2026-09-16T10:00:00Z",
  },
  {
    id: "enq-fixture-3",
    name: "Arvind Nair",
    company: "Coastal Builders",
    email_masked: "ar••••@coastal.example.in",
    phone_masked: "••••••7765",
    gstin: "32AAACC1206D1ZM",
    requirement: "Cement — OPC 53 grade, 40 bags to Kochi.",
    quantity: "40 bags",
    lines: [],
    status: "won",
    created_at: "2026-09-01T08:00:00Z",
    updated_at: "2026-09-05T11:00:00Z",
  },
];

let fixtureEnquiries: EnquiryOut[] = SEED.map((e) => ({ ...e }));

function paginate<T>(items: T[], page: number, size: number): PagedResult<T> {
  return paginateFixture(items, page, size, { pagesWhenEmpty: 1 });
}

export async function listEnquiries(params: ListEnquiriesParams = {}): Promise<PagedResult<EnquiryOut>> {
  const page = params.page ?? 1;
  const size = params.size ?? 25;
  if (USE_FIXTURES) {
    let rows = [...fixtureEnquiries];
    if (params.status) rows = rows.filter((e) => e.status === params.status);
    if (params.q) {
      const q = params.q.toLowerCase();
      rows = rows.filter((e) => e.name.toLowerCase().includes(q) || e.company.toLowerCase().includes(q));
    }
    rows.sort((a, b) => (a.created_at < b.created_at ? 1 : -1));
    return settle(paginate(rows, page, size), 200);
  }
  const qs = new URLSearchParams();
  qs.set("page", String(page));
  qs.set("size", String(size));
  if (params.status) qs.set("status", params.status);
  if (params.q) qs.set("q", params.q);
  if (params.since) qs.set("since", params.since);
  return apiRequest<PagedResult<EnquiryOut>>(`/storefront/enquiries?${qs.toString()}`);
}

export async function getEnquiry(id: string): Promise<EnquiryOut> {
  if (USE_FIXTURES) {
    const found = fixtureEnquiries.find((e) => e.id === id);
    if (!found) throw new ApiError(404, "That enquiry no longer exists.", null);
    return settle(found, 160);
  }
  return apiRequest<EnquiryOut>(`/storefront/enquiries/${id}`);
}

/** `PATCH /storefront/enquiries/{id}` — only `status`, and only `new -> contacted -> won | lost`.
 *  An illegal move is a 409 `enquiry_transition_invalid`, mirrored here in fixture mode too. */
export async function updateEnquiryStatus(id: string, status: EnquiryStatus): Promise<EnquiryOut> {
  if (USE_FIXTURES) {
    const found = fixtureEnquiries.find((e) => e.id === id);
    if (!found) throw new ApiError(404, "That enquiry no longer exists.", null);
    if (!ENQUIRY_TRANSITIONS[found.status].includes(status)) {
      throw new ApiError(
        409,
        `An enquiry can't move from "${found.status}" to "${status}".`,
        null,
      );
    }
    const updated: EnquiryOut = { ...found, status, updated_at: new Date().toISOString() };
    fixtureEnquiries = fixtureEnquiries.map((e) => (e.id === id ? updated : e));
    return settle(updated, 220);
  }
  return apiRequest<EnquiryOut>(`/storefront/enquiries/${id}`, { method: "PATCH", body: { status } });
}

/** `DELETE /storefront/enquiries/{id}` — erases the enquiry, its sealed contact details and its
 *  WhatsApp opt-in for good (a buyer's deletion request). Needs `storefront.sales.enquiry.delete`. */
export async function deleteEnquiry(id: string): Promise<void> {
  if (USE_FIXTURES) {
    if (!fixtureEnquiries.some((e) => e.id === id)) throw new ApiError(404, "That enquiry no longer exists.", null);
    fixtureEnquiries = fixtureEnquiries.filter((e) => e.id !== id);
    return settle(undefined, 220);
  }
  return apiRequest<void>(`/storefront/enquiries/${id}`, { method: "DELETE" });
}

/** Put the fixture inbox back to its seed. Test-only; live mode holds no state to reset. */
export function resetEnquiryFixtures(): void {
  fixtureEnquiries = SEED.map((e) => ({ ...e }));
}
