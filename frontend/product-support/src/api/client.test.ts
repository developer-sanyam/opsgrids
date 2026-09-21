/**
 * Each write function's request shape against the wire: method, exact URL, exact JSON body.
 * `USE_FIXTURES` is false in this environment (no `VITE_USE_FIXTURES` set), so every call below
 * goes through the real `apiRequest` path — the same one `frontend/opsgrids-client/src/http.ts`
 * tests against a stubbed `fetch`.
 */
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  assignTicket,
  changeTicketStatus,
  createAgent,
  createTicket,
  deleteAgent,
  getOverview,
  getSlaPolicy,
  getTicket,
  listAgents,
  listTickets,
  replyToTicket,
  setSlaPolicy,
  updateAgent,
  updateTicket,
} from "./client";

function jsonResponse(status: number, body: unknown) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

function stubFetch(response: Response) {
  const fetchMock = vi.fn().mockResolvedValue(response);
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("listTickets", () => {
  it("sends page, size and sort, and only the filters that are set", async () => {
    const fetchMock = stubFetch(
      jsonResponse(200, { items: [], total: 0, page: 1, size: 25, pages: 0 }),
    );
    await listTickets({ search: "checkout", status: "open", sort: "priority", page: 2, size: 25 });

    const [url] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toContain("/api/v1/support/tickets?");
    expect(url).toContain("page=2");
    expect(url).toContain("size=25");
    expect(url).toContain("sort=priority");
    expect(url).toContain("q=checkout");
    expect(url).toContain("status=open");
    expect(url).not.toContain("priority=");
    expect(url).not.toContain("assignee=");
  });
});

describe("getTicket", () => {
  it("GETs the ticket by number", async () => {
    const fetchMock = stubFetch(jsonResponse(200, { id: "TK-4001" }));
    await getTicket("TK-4001");
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/v1/support/tickets/TK-4001");
    expect(init.method).toBe("GET");
  });
});

describe("createTicket", () => {
  it("POSTs exactly the TicketCreateIn shape", async () => {
    const fetchMock = stubFetch(jsonResponse(201, { id: "TK-4021" }));
    const payload = {
      subject: "Checkout is broken",
      requester: { name: "Helena Vogel", email: "helena@example.com", company: "Northwind" },
      priority: "urgent" as const,
      body: "Everything is on fire.",
    };
    await createTicket(payload);
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/v1/support/tickets");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body as string)).toEqual(payload);
  });
});

describe("updateTicket", () => {
  it("PATCHes only the ticket properties endpoint, with the given fields", async () => {
    const fetchMock = stubFetch(jsonResponse(200, { id: "TK-4001" }));
    await updateTicket("TK-4001", { priority: "high" });
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/v1/support/tickets/TK-4001");
    expect(init.method).toBe("PATCH");
    expect(JSON.parse(init.body as string)).toEqual({ priority: "high" });
  });
});

describe("changeTicketStatus", () => {
  it("POSTs {status} to the status endpoint", async () => {
    const fetchMock = stubFetch(jsonResponse(200, { id: "TK-4001", status: "solved" }));
    await changeTicketStatus("TK-4001", "solved");
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/v1/support/tickets/TK-4001/status");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body as string)).toEqual({ status: "solved" });
  });

  it("surfaces a 409 as an ApiError, never retried", async () => {
    stubFetch(
      new Response(
        JSON.stringify({ title: "Conflict", status: 409, detail: "A ticket cannot move from 'solved' to 'pending'" }),
        { status: 409, headers: { "content-type": "application/problem+json" } },
      ),
    );
    await expect(changeTicketStatus("TK-4001", "pending")).rejects.toMatchObject({
      status: 409,
      message: "A ticket cannot move from 'solved' to 'pending'",
    });
  });
});

describe("replyToTicket", () => {
  it("POSTs exactly {body, author_name, internal} — never author_type", async () => {
    const fetchMock = stubFetch(jsonResponse(201, { id: "TK-4001" }));
    await replyToTicket("TK-4001", { body: "On it.", author_name: "Priya Nair", internal: true });
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/v1/support/tickets/TK-4001/reply");
    expect(init.method).toBe("POST");
    const body = JSON.parse(init.body as string) as Record<string, unknown>;
    expect(body).toEqual({ body: "On it.", author_name: "Priya Nair", internal: true });
    expect(body.author_type).toBeUndefined();
  });
});

describe("assignTicket", () => {
  it("POSTs {assignee_id: null} to unassign", async () => {
    const fetchMock = stubFetch(jsonResponse(200, { id: "TK-4001", assignee_id: null }));
    await assignTicket("TK-4001", null);
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/v1/support/tickets/TK-4001/assign");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body as string)).toEqual({ assignee_id: null });
  });
});

describe("agents", () => {
  it("listAgents GETs /support/agents", async () => {
    const fetchMock = stubFetch(jsonResponse(200, []));
    await listAgents();
    const [url] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/v1/support/agents");
  });

  it("createAgent POSTs the AgentIn shape", async () => {
    const fetchMock = stubFetch(jsonResponse(201, { id: "ag-09" }));
    const payload = { name: "Noor Aziz", email: "noor@example.com", role: "agent" as const, team: "Tier 1", online: true };
    await createAgent(payload);
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/v1/support/agents");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body as string)).toEqual(payload);
  });

  it("updateAgent PATCHes /support/agents/{ref}", async () => {
    const fetchMock = stubFetch(jsonResponse(200, { id: "ag-01" }));
    await updateAgent("ag-01", { team: "Billing" });
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/v1/support/agents/ag-01");
    expect(init.method).toBe("PATCH");
    expect(JSON.parse(init.body as string)).toEqual({ team: "Billing" });
  });

  it("deleteAgent DELETEs /support/agents/{ref}", async () => {
    const fetchMock = stubFetch(new Response(null, { status: 204 }));
    await deleteAgent("ag-01");
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/v1/support/agents/ag-01");
    expect(init.method).toBe("DELETE");
  });
});

describe("SLA policy", () => {
  it("getSlaPolicy GETs /support/sla-policy", async () => {
    const fetchMock = stubFetch(jsonResponse(200, { name: "Standard", targets: [] }));
    await getSlaPolicy();
    const [url] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/v1/support/sla-policy");
  });

  it("renders a 404 as an ApiError rather than a thrown parse failure", async () => {
    stubFetch(
      new Response(JSON.stringify({ title: "Not Found", status: 404, detail: "No SLA policy is configured for this organization" }), {
        status: 404,
        headers: { "content-type": "application/problem+json" },
      }),
    );
    await expect(getSlaPolicy()).rejects.toMatchObject({ status: 404 });
  });

  it("setSlaPolicy PUTs the whole policy", async () => {
    const fetchMock = stubFetch(jsonResponse(200, { name: "Standard" }));
    const payload = {
      name: "Standard",
      business_hours: "Mon-Fri",
      coverage: "24x7 for urgent",
      targets: [
        { priority: "urgent" as const, first_response_min: 30, resolution_min: 240 },
        { priority: "high" as const, first_response_min: 60, resolution_min: 480 },
        { priority: "normal" as const, first_response_min: 240, resolution_min: 1440 },
        { priority: "low" as const, first_response_min: 480, resolution_min: 2880 },
      ],
    };
    await setSlaPolicy(payload);
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/v1/support/sla-policy");
    expect(init.method).toBe("PUT");
    expect(JSON.parse(init.body as string)).toEqual(payload);
  });
});

describe("getOverview", () => {
  it("GETs /support/overview", async () => {
    const fetchMock = stubFetch(
      jsonResponse(200, {
        open_tickets: 0,
        breaching_sla: 0,
        first_response_median_min: 0,
        csat_pct: null,
        volume: [],
        priority_breakdown: [],
        status_breakdown: [],
      }),
    );
    await getOverview();
    const [url] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/v1/support/overview");
  });
});
