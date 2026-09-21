/**
 * Live-mode client tests: `USE_FIXTURES` is off by default in this suite (it only turns on with
 * `VITE_USE_FIXTURES=true`, and tests don't set it), so every function below hits `apiRequest`,
 * asserting the exact method, URL and JSON body the gateway receives.
 */
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  createAudience,
  createCampaign,
  deleteAudience,
  deleteCampaign,
  getAudiences,
  getCampaign,
  getCampaigns,
  getCampaignWeekly,
  getChannels,
  getOverview,
  updateAudience,
  updateCampaign,
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

function requested(fetchMock: ReturnType<typeof vi.fn>): { url: string; init: RequestInit } {
  const call = fetchMock.mock.calls[0] as [string, RequestInit] | undefined;
  if (!call) throw new Error("fetch was never called");
  return { url: call[0], init: call[1] };
}

afterEach(() => {
  vi.unstubAllGlobals();
});

const PAGE_ENVELOPE = { items: [], total: 0, page: 1, size: 25, pages: 0 };

describe("getCampaigns", () => {
  it("passes q, channel, status and audience_id through to the query string", async () => {
    const fetchMock = stubFetch(PAGE_ENVELOPE);
    await getCampaigns({
      q: "diwali",
      channel: "search",
      status: "live",
      audience_id: "aud-1",
      page: 2,
      size: 10,
    });
    const { url, init } = requested(fetchMock);
    expect(url).toBe(
      "/api/v1/marketing/campaigns?page=2&size=10&q=diwali&channel=search&status=live&audience_id=aud-1",
    );
    expect(init.method).toBe("GET");
  });

  it("omits an 'all' filter rather than sending it literally", async () => {
    const fetchMock = stubFetch(PAGE_ENVELOPE);
    await getCampaigns({ channel: "all", status: "all" });
    const { url } = requested(fetchMock);
    expect(url).toBe("/api/v1/marketing/campaigns?page=1&size=25");
  });
});

describe("getCampaign", () => {
  it("calls GET /marketing/campaigns/:id", async () => {
    const fetchMock = stubFetch({ id: "cmp-1" });
    await getCampaign("cmp-1");
    const { url, init } = requested(fetchMock);
    expect(url).toBe("/api/v1/marketing/campaigns/cmp-1");
    expect(init.method).toBe("GET");
  });
});

describe("createCampaign", () => {
  it("POSTs the CampaignIn body verbatim", async () => {
    const fetchMock = stubFetch({ id: "cmp-1" }, 201);
    const body = {
      name: "Diwali Push",
      channel: "search" as const,
      audience_id: "aud-1",
      start_date: "2026-10-01",
      budget_minor: 125050,
      currency: "INR",
    };
    await createCampaign(body);
    const { url, init } = requested(fetchMock);
    expect(url).toBe("/api/v1/marketing/campaigns");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body as string)).toEqual(body);
  });
});

describe("updateCampaign", () => {
  it("PATCHes only the changed fields, including a status move", async () => {
    const fetchMock = stubFetch({ id: "cmp-1", status: "live" });
    await updateCampaign("cmp-1", { status: "live" });
    const { url, init } = requested(fetchMock);
    expect(url).toBe("/api/v1/marketing/campaigns/cmp-1");
    expect(init.method).toBe("PATCH");
    expect(JSON.parse(init.body as string)).toEqual({ status: "live" });
  });

  it("never sends currency — it cannot change once a campaign exists", async () => {
    const fetchMock = stubFetch({ id: "cmp-1" });
    await updateCampaign("cmp-1", { name: "Renamed", budget_minor: 500000 });
    const { init } = requested(fetchMock);
    const sent = JSON.parse(init.body as string) as Record<string, unknown>;
    expect(sent).not.toHaveProperty("currency");
    expect(sent).toEqual({ name: "Renamed", budget_minor: 500000 });
  });
});

describe("deleteCampaign", () => {
  it("DELETEs by id", async () => {
    const fetchMock = stubFetch(undefined, 204);
    await deleteCampaign("cmp-1");
    const { url, init } = requested(fetchMock);
    expect(url).toBe("/api/v1/marketing/campaigns/cmp-1");
    expect(init.method).toBe("DELETE");
  });
});

describe("getCampaignWeekly", () => {
  it("calls the campaign's performance endpoint", async () => {
    const fetchMock = stubFetch([]);
    await getCampaignWeekly("cmp-1");
    const { url, init } = requested(fetchMock);
    expect(url).toBe("/api/v1/marketing/campaigns/cmp-1/performance");
    expect(init.method).toBe("GET");
  });
});

describe("getAudiences", () => {
  it("passes q through to the query string", async () => {
    const fetchMock = stubFetch(PAGE_ENVELOPE);
    await getAudiences({ q: "enterprise", page: 3, size: 50 });
    const { url } = requested(fetchMock);
    expect(url).toBe("/api/v1/marketing/audiences?page=3&size=50&q=enterprise");
  });
});

describe("createAudience", () => {
  it("POSTs the AudienceIn body verbatim, including the required created_at date", async () => {
    const fetchMock = stubFetch({ id: "aud-1" }, 201);
    const body = { name: "Enterprise India", size: 4210, created_at: "2026-08-01" };
    await createAudience(body);
    const { url, init } = requested(fetchMock);
    expect(url).toBe("/api/v1/marketing/audiences");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body as string)).toEqual(body);
  });
});

describe("updateAudience", () => {
  it("PATCHes only the changed fields", async () => {
    const fetchMock = stubFetch({ id: "aud-1" });
    await updateAudience("aud-1", { name: "Renamed segment" });
    const { url, init } = requested(fetchMock);
    expect(url).toBe("/api/v1/marketing/audiences/aud-1");
    expect(init.method).toBe("PATCH");
    expect(JSON.parse(init.body as string)).toEqual({ name: "Renamed segment" });
  });
});

describe("deleteAudience", () => {
  it("DELETEs by id", async () => {
    const fetchMock = stubFetch(undefined, 204);
    await deleteAudience("aud-1");
    const { url, init } = requested(fetchMock);
    expect(url).toBe("/api/v1/marketing/audiences/aud-1");
    expect(init.method).toBe("DELETE");
  });
});

describe("getChannels", () => {
  it("calls GET /marketing/channels with no query", async () => {
    const fetchMock = stubFetch([]);
    await getChannels();
    const { url, init } = requested(fetchMock);
    expect(url).toBe("/api/v1/marketing/channels");
    expect(init.method).toBe("GET");
  });
});

describe("getOverview", () => {
  it("calls GET /marketing/overview with no query", async () => {
    const fetchMock = stubFetch({
      currency: "INR",
      revenue_minor: 0,
      spend_minor: 0,
      pipeline_minor: 0,
      leads: 0,
      conversions: 0,
      roas: 0,
      cpl_minor: null,
      revenue_vs_spend: [],
      channels: [],
    });
    await getOverview();
    const { url, init } = requested(fetchMock);
    expect(url).toBe("/api/v1/marketing/overview");
    expect(init.method).toBe("GET");
  });
});
