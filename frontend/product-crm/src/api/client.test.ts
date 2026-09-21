/**
 * Live-mode client tests: the app's `USE_FIXTURES` is off by default in this suite (it only
 * turns on with `VITE_USE_FIXTURES=true`, and tests don't set it), so every function below hits
 * `apiRequest`, and these assert the exact path the gateway actually mounts — `/customers`,
 * `/customers/owners` and `/deals/activities`, not the `/companies`, `/owners` and `/activities`
 * paths this app called before the CRM backend existed.
 */
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  getAllDeals,
  getCompany,
  getContact,
  getDeal,
  getOwners,
  listActivities,
  listCompanies,
  listContacts,
  listDeals,
} from "./client";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

function stubFetch(body: unknown, status = 200) {
  const fetchMock = vi.fn().mockResolvedValue(jsonResponse(body, status));
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function requestedUrl(fetchMock: ReturnType<typeof vi.fn>): string {
  const call = fetchMock.mock.calls[0] as [string, RequestInit] | undefined;
  if (!call) throw new Error("fetch was never called");
  return call[0];
}

afterEach(() => {
  vi.unstubAllGlobals();
});

const OWNER_WIRE = { id: "own-1", full_name: "Sofia Marchetti", email: "sofia@example.com", role: "AE" };

const COMPANY_WIRE = {
  id: "comp-1",
  name: "Northwind Logistics",
  domain: "northwind.co",
  industry: "Logistics",
  size_band: "501-1000",
  city: "Mumbai",
  country: "India",
  status: "customer",
  owner_id: "own-1",
  annual_revenue_minor: 840000000000,
  currency: "INR",
  created_at: "2024-02-11T09:00:00Z",
};

const CONTACT_WIRE = {
  id: "cont-1",
  full_name: "Asha Rao",
  title: "VP Ops",
  email: "asha@northwind.co",
  phone: "+91 •• •••• ••12",
  company_id: "comp-1",
  status: "customer",
  owner_id: "own-1",
  last_activity_at: null,
  created_at: "2024-02-12T09:00:00Z",
};

const DEAL_WIRE = {
  id: "deal-1",
  name: "Northwind renewal",
  company_id: "comp-1",
  primary_contact_id: "cont-1",
  value_minor: 500000000,
  currency: "INR",
  stage: "proposal",
  owner_id: "own-1",
  probability: 60,
  close_date: "2026-10-01",
  created_at: "2026-08-01T09:00:00Z",
  last_activity_at: "2026-09-01T09:00:00Z",
  source: "referral",
};

const ACTIVITY_WIRE = {
  id: "act-1",
  type: "call",
  subject: "Renewal check-in",
  body: null,
  deal_id: "deal-1",
  company_id: "comp-1",
  contact_id: "cont-1",
  owner_id: "own-1",
  occurred_at: "2026-09-01T09:00:00Z",
  direction: "outbound",
};

describe("listCompanies", () => {
  it("calls GET /customers, not /companies — that's a customer row in the customer service", async () => {
    const fetchMock = stubFetch({ items: [COMPANY_WIRE], total: 1, page: 1, size: 200, pages: 1 });
    const result = await listCompanies({ search: "north", status: "customer" });

    const url = requestedUrl(fetchMock);
    expect(url).toContain("/api/v1/customers?");
    expect(url).not.toMatch(/\/api\/v1\/companies/);
    const params = new URL(url, "http://localhost").searchParams;
    expect(params.get("q")).toBe("north");
    expect(params.get("status")).toBe("customer");
    expect(params.get("size")).toBe("200");

    expect(result).toEqual({ items: [{ ...COMPANY_WIRE }], total: 1 });
  });

  it("omits status when 'all'", async () => {
    const fetchMock = stubFetch({ items: [], total: 0, page: 1, size: 200, pages: 0 });
    await listCompanies({ status: "all" });
    const params = new URL(requestedUrl(fetchMock), "http://localhost").searchParams;
    expect(params.has("status")).toBe(false);
  });
});

describe("getCompany", () => {
  it("calls GET /customers/:id", async () => {
    const fetchMock = stubFetch(COMPANY_WIRE);
    const result = await getCompany("comp-1");
    expect(requestedUrl(fetchMock)).toBe("/api/v1/customers/comp-1");
    expect(result).toEqual(COMPANY_WIRE);
  });
});

describe("getOwners", () => {
  it("calls GET /customers/owners, not a standalone /owners endpoint", async () => {
    const fetchMock = stubFetch([OWNER_WIRE]);
    const result = await getOwners();
    expect(requestedUrl(fetchMock)).toBe("/api/v1/customers/owners");
    expect(result).toEqual([OWNER_WIRE]);
  });
});

describe("listContacts", () => {
  it("calls GET /contacts with q/status", async () => {
    const fetchMock = stubFetch({ items: [CONTACT_WIRE], total: 1, page: 1, size: 200, pages: 1 });
    const result = await listContacts({ search: "asha", status: "customer" });
    const url = requestedUrl(fetchMock);
    expect(url).toContain("/api/v1/contacts?");
    const params = new URL(url, "http://localhost").searchParams;
    expect(params.get("q")).toBe("asha");
    expect(params.get("status")).toBe("customer");
    expect(result).toEqual({ items: [CONTACT_WIRE], total: 1 });
  });
});

describe("getContact", () => {
  it("calls GET /contacts/:id", async () => {
    const fetchMock = stubFetch(CONTACT_WIRE);
    await getContact("cont-1");
    expect(requestedUrl(fetchMock)).toBe("/api/v1/contacts/cont-1");
  });
});

describe("listDeals", () => {
  it("calls GET /deals with q/stage/owner_id", async () => {
    const fetchMock = stubFetch({ items: [DEAL_WIRE], total: 1, page: 1, size: 200, pages: 1 });
    const result = await listDeals({ search: "renewal", stage: "proposal", ownerId: "own-1" });
    const url = requestedUrl(fetchMock);
    expect(url).toContain("/api/v1/deals?");
    const params = new URL(url, "http://localhost").searchParams;
    expect(params.get("q")).toBe("renewal");
    expect(params.get("stage")).toBe("proposal");
    expect(params.get("owner_id")).toBe("own-1");
    expect(result).toEqual({ items: [DEAL_WIRE], total: 1 });
  });
});

describe("getAllDeals", () => {
  it("calls GET /deals?size=<full page>", async () => {
    const fetchMock = stubFetch({ items: [DEAL_WIRE], total: 1, page: 1, size: 200, pages: 1 });
    await getAllDeals();
    expect(requestedUrl(fetchMock)).toBe("/api/v1/deals?size=200");
  });
});

describe("getDeal", () => {
  it("calls GET /deals/:id", async () => {
    const fetchMock = stubFetch(DEAL_WIRE);
    await getDeal("deal-1");
    expect(requestedUrl(fetchMock)).toBe("/api/v1/deals/deal-1");
  });
});

describe("listActivities", () => {
  it("calls GET /deals/activities, not a standalone /activities endpoint", async () => {
    const fetchMock = stubFetch({ items: [ACTIVITY_WIRE], total: 1, page: 1, size: 200, pages: 1 });
    const result = await listActivities({ dealId: "deal-1", type: "call" });
    const url = requestedUrl(fetchMock);
    expect(url).toContain("/api/v1/deals/activities?");
    expect(url).not.toMatch(/\/api\/v1\/activities\?/);
    const params = new URL(url, "http://localhost").searchParams;
    expect(params.get("deal_id")).toBe("deal-1");
    expect(params.get("type")).toBe("call");
    expect(result).toEqual([ACTIVITY_WIRE]);
  });
});

describe("error handling", () => {
  it("raises ApiError with the RFC 7807 detail on a 403", async () => {
    stubFetch({ title: "Forbidden", status: 403, detail: "You don't have this permission." }, 403);
    await expect(listCompanies()).rejects.toMatchObject({
      name: "ApiError",
      status: 403,
      message: "You don't have this permission.",
    });
  });
});
