/**
 * Each write function's request shape against the wire: method, exact URL, exact JSON body.
 * `USE_FIXTURES` is false in this environment (no `VITE_USE_FIXTURES` set), so every call below
 * goes through the real `apiRequest` path against a stubbed `fetch`.
 */
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  addWorklog,
  changeIncidentStatus,
  createAsset,
  createCatalogItem,
  createIncident,
  createRequest,
  decideRequest,
  deleteAsset,
  deleteCatalogItem,
  deleteIncident,
  deleteRequest,
  fulfilRequest,
  getOverview,
  listIncidents,
  updateAsset,
  updateCatalogItem,
  updateIncident,
  updateRequest,
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

describe("listIncidents", () => {
  it("sends page and size, and only the filters that are set", async () => {
    const fetchMock = stubFetch(
      jsonResponse(200, { items: [], total: 0, page: 1, size: 25, pages: 0 }),
    );
    await listIncidents({ search: "core-sw", priority: "P1", page: 2, size: 25 });

    const [url] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toContain("/api/v1/itsm/incidents?");
    expect(url).toContain("page=2");
    expect(url).toContain("size=25");
    expect(url).toContain("q=core-sw");
    expect(url).toContain("priority=P1");
    expect(url).not.toContain("status=");
    expect(url).not.toContain("sort=");
  });
});

describe("createIncident", () => {
  it("POSTs exactly the IncidentIn shape", async () => {
    const fetchMock = stubFetch(jsonResponse(201, { id: "INC-9001" }));
    const payload = {
      summary: "VPN down for finance team",
      description: "",
      priority: "P2" as const,
      category: "network" as const,
      requester: "Priya Nair",
      assignee: "",
      affected_asset_tag: null,
    };
    await createIncident(payload);

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/v1/itsm/incidents");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body as string)).toEqual(payload);
  });
});

describe("addWorklog", () => {
  it("POSTs only author and body — never a client-chosen kind", async () => {
    const fetchMock = stubFetch(jsonResponse(200, { id: "INC-4821" }));
    await addWorklog("INC-4821", { author: "Meera Raghavan", body: "Checked in with the vendor." });

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/v1/itsm/incidents/INC-4821/worklog");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body as string)).toEqual({
      author: "Meera Raghavan",
      body: "Checked in with the vendor.",
    });
  });
});

describe("updateIncident", () => {
  it("PATCHes only the fields given", async () => {
    const fetchMock = stubFetch(jsonResponse(200, { id: "INC-4821" }));
    await updateIncident("INC-4821", { priority: "P1" });

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/v1/itsm/incidents/INC-4821");
    expect(init.method).toBe("PATCH");
    expect(JSON.parse(init.body as string)).toEqual({ priority: "P1" });
  });
});

describe("changeIncidentStatus", () => {
  it("POSTs status and note to /status", async () => {
    const fetchMock = stubFetch(jsonResponse(200, { id: "INC-4821", status: "resolved" }));
    await changeIncidentStatus("INC-4821", { status: "resolved", note: "Switched to standby." });

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/v1/itsm/incidents/INC-4821/status");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body as string)).toEqual({
      status: "resolved",
      note: "Switched to standby.",
    });
  });
});

describe("deleteIncident", () => {
  it("DELETEs by number", async () => {
    const fetchMock = stubFetch(new Response(null, { status: 204 }));
    await deleteIncident("INC-4821");

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/v1/itsm/incidents/INC-4821");
    expect(init.method).toBe("DELETE");
  });
});

describe("createRequest", () => {
  it("POSTs exactly the ServiceRequestIn shape", async () => {
    const fetchMock = stubFetch(jsonResponse(201, { id: "REQ-3001" }));
    const payload = { catalog_item_id: "CAT-04", subject: "Staging VPN", requester: "Tobias Berg" };
    await createRequest(payload);

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/v1/itsm/service-requests");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body as string)).toEqual(payload);
  });
});

describe("decideRequest", () => {
  it("POSTs the decision and note", async () => {
    const fetchMock = stubFetch(jsonResponse(200, { id: "REQ-2051", status: "rejected" }));
    await decideRequest("REQ-2051", { decision: "reject", note: "No budget this quarter." });

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/v1/itsm/service-requests/REQ-2051/decision");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body as string)).toEqual({
      decision: "reject",
      note: "No budget this quarter.",
    });
  });
});

describe("fulfilRequest", () => {
  it("POSTs to /fulfil with no body", async () => {
    const fetchMock = stubFetch(jsonResponse(200, { id: "REQ-2050", status: "fulfilled" }));
    await fulfilRequest("REQ-2050");

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/v1/itsm/service-requests/REQ-2050/fulfil");
    expect(init.method).toBe("POST");
    expect(init.body).toBeUndefined();
  });
});

describe("updateRequest", () => {
  it("PATCHes assignee, including a null to unassign", async () => {
    const fetchMock = stubFetch(jsonResponse(200, { id: "REQ-2050" }));
    await updateRequest("REQ-2050", { assignee: null });

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/v1/itsm/service-requests/REQ-2050");
    expect(init.method).toBe("PATCH");
    expect(JSON.parse(init.body as string)).toEqual({ assignee: null });
  });
});

describe("deleteRequest", () => {
  it("DELETEs by number", async () => {
    const fetchMock = stubFetch(new Response(null, { status: 204 }));
    await deleteRequest("REQ-2050");

    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(init.method).toBe("DELETE");
  });
});

describe("catalog writes", () => {
  it("POSTs a new item with its code", async () => {
    const fetchMock = stubFetch(jsonResponse(201, { id: "CAT-11" }));
    const payload = { id: "CAT-11", name: "Standing desk", category: "hardware" as const };
    await createCatalogItem(payload);

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/v1/itsm/catalog");
    expect(JSON.parse(init.body as string)).toEqual(payload);
  });

  it("PATCHes an item without its code", async () => {
    const fetchMock = stubFetch(jsonResponse(200, { id: "CAT-11" }));
    await updateCatalogItem("CAT-11", { sla_days: 3 });

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/v1/itsm/catalog/CAT-11");
    expect(init.method).toBe("PATCH");
    expect(JSON.parse(init.body as string)).toEqual({ sla_days: 3 });
  });

  it("DELETEs (retires) an item", async () => {
    const fetchMock = stubFetch(new Response(null, { status: 204 }));
    await deleteCatalogItem("CAT-11");

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/v1/itsm/catalog/CAT-11");
    expect(init.method).toBe("DELETE");
  });
});

describe("asset writes", () => {
  it("POSTs exactly the AssetIn shape", async () => {
    const fetchMock = stubFetch(jsonResponse(201, { tag: "AST-2001" }));
    const payload = { tag: "AST-2001", name: "Standing desk", type: "monitor" as const };
    await createAsset(payload);

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/v1/itsm/assets");
    expect(JSON.parse(init.body as string)).toEqual(payload);
  });

  it("PATCHes an asset without its tag", async () => {
    const fetchMock = stubFetch(jsonResponse(200, { tag: "AST-1001" }));
    await updateAsset("AST-1001", { status: "retired" });

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/v1/itsm/assets/AST-1001");
    expect(init.method).toBe("PATCH");
    expect(JSON.parse(init.body as string)).toEqual({ status: "retired" });
    expect(JSON.parse(init.body as string)).not.toHaveProperty("tag");
  });

  it("DELETEs an asset by tag", async () => {
    const fetchMock = stubFetch(new Response(null, { status: 204 }));
    await deleteAsset("AST-1001");

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/v1/itsm/assets/AST-1001");
    expect(init.method).toBe("DELETE");
  });
});

describe("getOverview", () => {
  it("GETs the rollup with no query string", async () => {
    const fetchMock = stubFetch(
      jsonResponse(200, {
        open_incidents: 0,
        sla_compliance_pct: 100,
        requests_awaiting_approval: 0,
        assets_under_management: 0,
        volume: [],
        by_priority: [],
        by_category: [],
      }),
    );
    await getOverview();

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/v1/itsm/overview");
    expect(init.method ?? "GET").toBe("GET");
  });
});
