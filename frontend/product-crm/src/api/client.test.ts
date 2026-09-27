/**
 * Live-mode client tests: the app's `USE_FIXTURES` is off by default in this suite (it only
 * turns on with `VITE_USE_FIXTURES=true`, and tests don't set it), so every function below hits
 * `apiRequest`, and these assert the exact path the gateway actually mounts — `/customers`,
 * `/customers/owners` and `/deals/activities`, not the `/companies`, `/owners` and `/activities`
 * paths this app called before the CRM backend existed.
 */
import { afterEach, describe, expect, it, vi } from "vitest";

import { getAccessToken, setAccessToken, setUnauthorizedHandler } from "@opsgrids/client";

import {
  createCompany,
  createContact,
  createDeal,
  createOwner,
  deleteCompany,
  deleteContact,
  deleteDeal,
  getAllDeals,
  getCompany,
  getContact,
  getDeal,
  getOwners,
  getSummary,
  listActivities,
  listCompanies,
  listContacts,
  listDeals,
  logActivity,
  moveDealStage,
  revealContactPhone,
  revealLeadField,
  setContactPhone,
  updateCompany,
  updateContact,
  updateDeal,
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

function requested(fetchMock: ReturnType<typeof vi.fn>): { url: string; init: RequestInit } {
  const call = fetchMock.mock.calls[0] as [string, RequestInit] | undefined;
  if (!call) throw new Error("fetch was never called");
  return { url: call[0], init: call[1] };
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

const PAGE_ENVELOPE = { page: 1, size: 25, pages: 1 };

describe("listCompanies", () => {
  it("calls GET /customers, not /companies — that's a customer row in the customer service", async () => {
    const fetchMock = stubFetch({ items: [COMPANY_WIRE], total: 1, ...PAGE_ENVELOPE });
    const result = await listCompanies({ search: "north", status: "customer" });

    const url = requestedUrl(fetchMock);
    expect(url).toContain("/api/v1/customers?");
    expect(url).not.toMatch(/\/api\/v1\/companies/);
    const params = new URL(url, "http://localhost").searchParams;
    expect(params.get("q")).toBe("north");
    expect(params.get("status")).toBe("customer");
    expect(params.get("page")).toBe("1");
    expect(params.get("size")).toBe("25");

    expect(result).toEqual({ items: [{ ...COMPANY_WIRE }], total: 1, ...PAGE_ENVELOPE });
  });

  it("omits status when 'all', and forwards an explicit page/size", async () => {
    const fetchMock = stubFetch({ items: [], total: 0, page: 2, size: 50, pages: 0 });
    await listCompanies({ status: "all", page: 2, size: 50 });
    const params = new URL(requestedUrl(fetchMock), "http://localhost").searchParams;
    expect(params.has("status")).toBe(false);
    expect(params.get("page")).toBe("2");
    expect(params.get("size")).toBe("50");
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

describe("createCompany", () => {
  it("POSTs to /customers with the CompanyIn body verbatim", async () => {
    const fetchMock = stubFetch(COMPANY_WIRE, 201);
    const body = { name: "Northwind Logistics", owner_id: "own-1", status: "prospect" as const };
    await createCompany(body);
    const { url, init } = requested(fetchMock);
    expect(url).toBe("/api/v1/customers");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body as string)).toEqual(body);
  });
});

describe("updateCompany", () => {
  it("PATCHes /customers/:id with only the changed fields", async () => {
    const fetchMock = stubFetch(COMPANY_WIRE);
    await updateCompany("comp-1", { status: "churned" });
    const { url, init } = requested(fetchMock);
    expect(url).toBe("/api/v1/customers/comp-1");
    expect(init.method).toBe("PATCH");
    expect(JSON.parse(init.body as string)).toEqual({ status: "churned" });
  });
});

describe("deleteCompany", () => {
  it("DELETEs /customers/:id", async () => {
    const fetchMock = stubFetch(undefined, 204);
    await deleteCompany("comp-1");
    const { url, init } = requested(fetchMock);
    expect(url).toBe("/api/v1/customers/comp-1");
    expect(init.method).toBe("DELETE");
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

describe("createOwner", () => {
  it("POSTs to /customers/owners with the OwnerIn body verbatim", async () => {
    const fetchMock = stubFetch(OWNER_WIRE, 201);
    const body = { full_name: "Sofia Marchetti", email: "sofia@example.com", role: "AE" };
    await createOwner(body);
    const { url, init } = requested(fetchMock);
    expect(url).toBe("/api/v1/customers/owners");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body as string)).toEqual(body);
  });
});

describe("listContacts", () => {
  it("calls GET /contacts with q/status/company_id/page/size", async () => {
    const fetchMock = stubFetch({ items: [CONTACT_WIRE], total: 1, ...PAGE_ENVELOPE });
    const result = await listContacts({ search: "asha", status: "customer", companyId: "comp-1" });
    const url = requestedUrl(fetchMock);
    expect(url).toContain("/api/v1/contacts?");
    const params = new URL(url, "http://localhost").searchParams;
    expect(params.get("q")).toBe("asha");
    expect(params.get("status")).toBe("customer");
    expect(params.get("company_id")).toBe("comp-1");
    expect(result).toEqual({ items: [CONTACT_WIRE], total: 1, ...PAGE_ENVELOPE });
  });
});

describe("getContact", () => {
  it("calls GET /contacts/:id", async () => {
    const fetchMock = stubFetch(CONTACT_WIRE);
    await getContact("cont-1");
    expect(requestedUrl(fetchMock)).toBe("/api/v1/contacts/cont-1");
  });
});

describe("createContact", () => {
  it("POSTs to /contacts with the ContactIn body verbatim, no phone field", async () => {
    const fetchMock = stubFetch(CONTACT_WIRE, 201);
    const body = {
      full_name: "Asha Rao",
      email: "asha@northwind.co",
      company_id: "comp-1",
      owner_id: "own-1",
      raw_phone: "+919876543210",
    };
    await createContact(body);
    const { url, init } = requested(fetchMock);
    expect(url).toBe("/api/v1/contacts");
    expect(init.method).toBe("POST");
    const sent = JSON.parse(init.body as string) as Record<string, unknown>;
    expect(sent).toEqual(body);
    expect(sent.phone).toBeUndefined();
  });
});

describe("updateContact", () => {
  it("PATCHes /contacts/:id and never carries phone or raw_phone", async () => {
    const fetchMock = stubFetch(CONTACT_WIRE);
    await updateContact("cont-1", { full_name: "Asha K. Rao" });
    const { url, init } = requested(fetchMock);
    expect(url).toBe("/api/v1/contacts/cont-1");
    expect(init.method).toBe("PATCH");
    const sent = JSON.parse(init.body as string) as Record<string, unknown>;
    expect(sent).toEqual({ full_name: "Asha K. Rao" });
    expect(sent.phone).toBeUndefined();
    expect(sent.raw_phone).toBeUndefined();
  });
});

describe("deleteContact", () => {
  it("DELETEs /contacts/:id", async () => {
    const fetchMock = stubFetch(undefined, 204);
    await deleteContact("cont-1");
    const { url, init } = requested(fetchMock);
    expect(url).toBe("/api/v1/contacts/cont-1");
    expect(init.method).toBe("DELETE");
  });
});

describe("setContactPhone", () => {
  it("PUTs /contacts/:id/phone with raw_phone, and returns the mask never the raw value", async () => {
    const fetchMock = stubFetch(CONTACT_WIRE);
    const result = await setContactPhone("cont-1", { raw_phone: "+919876543210" });
    const { url, init } = requested(fetchMock);
    expect(url).toBe("/api/v1/contacts/cont-1/phone");
    expect(init.method).toBe("PUT");
    expect(JSON.parse(init.body as string)).toEqual({ raw_phone: "+919876543210" });
    expect(result.phone).toBe(CONTACT_WIRE.phone);
  });
});

describe("listDeals", () => {
  it("calls GET /deals with q/stage/owner_id/company_id", async () => {
    const fetchMock = stubFetch({ items: [DEAL_WIRE], total: 1, ...PAGE_ENVELOPE });
    const result = await listDeals({
      search: "renewal",
      stage: "proposal",
      ownerId: "own-1",
      companyId: "comp-1",
    });
    const url = requestedUrl(fetchMock);
    expect(url).toContain("/api/v1/deals?");
    const params = new URL(url, "http://localhost").searchParams;
    expect(params.get("q")).toBe("renewal");
    expect(params.get("stage")).toBe("proposal");
    expect(params.get("owner_id")).toBe("own-1");
    expect(params.get("company_id")).toBe("comp-1");
    expect(result).toEqual({ items: [DEAL_WIRE], total: 1, ...PAGE_ENVELOPE });
  });
});

describe("getAllDeals", () => {
  it("calls GET /deals?size=<full page>", async () => {
    const fetchMock = stubFetch({ items: [DEAL_WIRE], total: 1, ...PAGE_ENVELOPE });
    await getAllDeals();
    expect(requestedUrl(fetchMock)).toBe("/api/v1/deals?size=200");
  });

  it("narrows to one rep with ownerId", async () => {
    const fetchMock = stubFetch({ items: [DEAL_WIRE], total: 1, ...PAGE_ENVELOPE });
    await getAllDeals({ ownerId: "own-1" });
    const params = new URL(requestedUrl(fetchMock), "http://localhost").searchParams;
    expect(params.get("owner_id")).toBe("own-1");
  });
});

describe("getDeal", () => {
  it("calls GET /deals/:id", async () => {
    const fetchMock = stubFetch(DEAL_WIRE);
    await getDeal("deal-1");
    expect(requestedUrl(fetchMock)).toBe("/api/v1/deals/deal-1");
  });
});

describe("createDeal", () => {
  it("POSTs to /deals with the DealIn body verbatim", async () => {
    const fetchMock = stubFetch(DEAL_WIRE, 201);
    const body = {
      name: "Northwind renewal",
      company_id: "comp-1",
      owner_id: "own-1",
      value_minor: 500000000,
      close_date: "2026-10-01",
    };
    await createDeal(body);
    const { url, init } = requested(fetchMock);
    expect(url).toBe("/api/v1/deals");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body as string)).toEqual(body);
  });
});

describe("updateDeal", () => {
  it("PATCHes /deals/:id and never carries stage", async () => {
    const fetchMock = stubFetch(DEAL_WIRE);
    await updateDeal("deal-1", { value_minor: 600000000 });
    const { url, init } = requested(fetchMock);
    expect(url).toBe("/api/v1/deals/deal-1");
    expect(init.method).toBe("PATCH");
    const sent = JSON.parse(init.body as string) as Record<string, unknown>;
    expect(sent).toEqual({ value_minor: 600000000 });
    expect(sent.stage).toBeUndefined();
  });
});

describe("deleteDeal", () => {
  it("DELETEs /deals/:id", async () => {
    const fetchMock = stubFetch(undefined, 204);
    await deleteDeal("deal-1");
    const { url, init } = requested(fetchMock);
    expect(url).toBe("/api/v1/deals/deal-1");
    expect(init.method).toBe("DELETE");
  });
});

describe("moveDealStage", () => {
  it("POSTs /deals/:id/stage with the StageMoveIn body", async () => {
    const fetchMock = stubFetch(DEAL_WIRE);
    await moveDealStage("deal-1", { stage: "negotiation", probability: 70 });
    const { url, init } = requested(fetchMock);
    expect(url).toBe("/api/v1/deals/deal-1/stage");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body as string)).toEqual({ stage: "negotiation", probability: 70 });
  });
});

describe("getSummary", () => {
  function summaryBody(overrides: Record<string, unknown> = {}) {
    return {
      pipeline_value_minor: 1,
      weighted_pipeline_minor: 1,
      open_deals: 1,
      win_rate_pct: 50,
      won_value_minor: 0,
      activities_this_week: 3,
      currency: "INR",
      trend: null,
      funnel: [],
      ...overrides,
    };
  }

  it("calls GET /deals/summary with no query when unscoped", async () => {
    const fetchMock = stubFetch(summaryBody());
    const result = await getSummary();
    expect(requestedUrl(fetchMock)).toBe("/api/v1/deals/summary");
    expect(result.activities_this_week).toBe(3);
    expect(result.trend).toBeNull();
  });

  it("passes owner=me for the 'My pipeline' toggle", async () => {
    const fetchMock = stubFetch(summaryBody());
    await getSummary({ owner: "me" });
    expect(requestedUrl(fetchMock)).toBe("/api/v1/deals/summary?owner=me");
  });

  it("passes owner_id when a specific rep is named", async () => {
    const fetchMock = stubFetch(summaryBody());
    await getSummary({ ownerId: "own-1" });
    expect(requestedUrl(fetchMock)).toBe("/api/v1/deals/summary?owner_id=own-1");
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
    expect(result.items).toEqual([ACTIVITY_WIRE]);
  });
});

describe("logActivity", () => {
  it("POSTs to /deals/activities with the ActivityIn body verbatim", async () => {
    const fetchMock = stubFetch(ACTIVITY_WIRE, 201);
    const body = { type: "call" as const, subject: "Renewal check-in", deal_id: "deal-1" };
    await logActivity(body);
    const { url, init } = requested(fetchMock);
    expect(url).toBe("/api/v1/deals/activities");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body as string)).toEqual(body);
  });
});

describe("revealLeadField", () => {
  const STEP_UP_HEADERS = { Authorization: "Bearer step-up-token" };

  afterEach(() => {
    setAccessToken(null);
    setUnauthorizedHandler(null);
  });

  it("POSTs { field } to /leads/:id:reveal and returns the value", async () => {
    const fetchMock = stubFetch({ lead_id: "lead-1", field: "email", value: "meera@halcyontextiles.in" });
    const value = await revealLeadField("lead-1", "email", STEP_UP_HEADERS);
    const { url, init } = requested(fetchMock);
    expect(url).toBe("/api/v1/leads/lead-1:reveal");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body as string)).toEqual({ field: "email" });
    expect(value).toBe("meera@halcyontextiles.in");
  });

  it("presents the step-up token as the only credential, never the session token", async () => {
    setAccessToken("session-token");
    const fetchMock = stubFetch({ lead_id: "lead-1", field: "phone", value: "+919876543210" });
    await revealLeadField("lead-1", "phone", STEP_UP_HEADERS);
    const headers = requested(fetchMock).init.headers as Record<string, string>;
    expect(headers.Authorization).toBe("Bearer step-up-token");
    expect(JSON.stringify(headers)).not.toContain("session-token");
  });

  it("does not renew the session or sign the person out when the step-up token is refused with a 401", async () => {
    setAccessToken("session-token");
    const unauthorized = vi.fn();
    setUnauthorizedHandler(unauthorized);
    const fetchMock = stubFetch({ title: "Unauthorized", status: 401, detail: "Token has expired" }, 401);

    await expect(revealLeadField("lead-1", "phone", STEP_UP_HEADERS)).rejects.toMatchObject({
      name: "ApiError",
      status: 401,
    });

    expect(unauthorized).not.toHaveBeenCalled();
    expect(getAccessToken()).toBe("session-token");
    // One call, and no attempt at /auth/refresh.
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("keeps the server's sentence on the error for a refusal", async () => {
    stubFetch({ title: "Forbidden", status: 403, detail: "Out of scope for this resource" }, 403);
    await expect(revealLeadField("lead-1", "phone", STEP_UP_HEADERS)).rejects.toMatchObject({
      status: 403,
      message: "Out of scope for this resource",
    });
  });
});

describe("revealContactPhone", () => {
  const STEP_UP_HEADERS = { Authorization: "Bearer step-up-token" };

  afterEach(() => {
    setAccessToken(null);
  });

  it("POSTs to /contacts/:id/phone:reveal with no body, the step-up token as the only credential, and returns the number", async () => {
    setAccessToken("session-token");
    const fetchMock = stubFetch({ contact_id: "cont-1", phone: "+919876543210" });
    const phone = await revealContactPhone("cont-1", STEP_UP_HEADERS);
    const { url, init } = requested(fetchMock);
    expect(url).toBe("/api/v1/contacts/cont-1/phone:reveal");
    expect(init.method).toBe("POST");
    expect(init.body).toBeUndefined();
    const headers = init.headers as Record<string, string>;
    expect(headers.Authorization).toBe("Bearer step-up-token");
    expect(JSON.stringify(headers)).not.toContain("session-token");
    expect(phone).toBe("+919876543210");
  });

  it("returns null when the contact has no sealed number", async () => {
    stubFetch({ contact_id: "cont-1", phone: null });
    expect(await revealContactPhone("cont-1", STEP_UP_HEADERS)).toBeNull();
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
