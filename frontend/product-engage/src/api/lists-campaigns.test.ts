import { afterEach, describe, expect, it, vi } from "vitest";

import {
  addListMembers,
  cancelCampaign,
  createCampaign,
  createList,
  deleteCampaign,
  deleteList,
  editCampaign,
  editList,
  getCampaign,
  getList,
  listCampaignRecipients,
  listCampaigns,
  listListMembers,
  listLists,
  removeListMembers,
  resumeCampaign,
  sendCampaign,
} from "./client";
import { CAMPAIGN_RECIPIENTS, CAMPAIGNS, CONTACT_LISTS } from "./fixtures";

/**
 * The wire for the audience-and-campaign half of the engage router: the gateway mounts the service
 * at `/api/v1/engage`, so a path that drifts by one segment fails as a 404 the UI reports as
 * "couldn't load" with nothing naming the cause, and a method that drifts (a `PUT` where the
 * service has a `PATCH`) fails as a 405 on the first edit. The fixtures are held to the service's
 * own shapes for the same reason `fixtures.test.ts` holds the connection to its six keys.
 */

function stubFetch(body: unknown = {}, status = 200) {
  const noBody = status === 204;
  const fetchMock = vi.fn().mockResolvedValue(
    new Response(noBody ? null : JSON.stringify(body), {
      status,
      headers: noBody ? {} : { "content-type": "application/json" },
    }),
  );
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function call(fetchMock: ReturnType<typeof vi.fn>): [string, RequestInit] {
  const first = fetchMock.mock.calls[0] as [string, RequestInit] | undefined;
  if (!first) throw new Error("fetch was never called");
  return first;
}

function bodyOf(init: RequestInit): unknown {
  return typeof init.body === "string" ? JSON.parse(init.body) : init.body;
}

const PAGE = { items: [], total: 0, page: 1, size: 25, pages: 0 };
const ID = "9a1b2c3d-4e5f-4061-8a7b-1c2d3e4f5061";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("the list paths", () => {
  it("reads a page of lists with the caller's paging", async () => {
    const fetchMock = stubFetch(PAGE);
    await listLists({ page: 2, size: 50 });
    const [url, init] = call(fetchMock);
    expect(url).toBe("/api/v1/engage/lists?page=2&size=50");
    expect(init.method).toBe("GET");
  });

  it("creates with POST and renames with PATCH", async () => {
    let fetchMock = stubFetch({});
    await createList({ name: "Enterprise", description: "" });
    let [url, init] = call(fetchMock);
    expect(url).toBe("/api/v1/engage/lists");
    expect(init.method).toBe("POST");
    expect(bodyOf(init)).toEqual({ name: "Enterprise", description: "" });

    fetchMock = stubFetch({});
    await editList(ID, { name: "Enterprise accounts" });
    [url, init] = call(fetchMock);
    expect(url).toBe(`/api/v1/engage/lists/${ID}`);
    expect(init.method).toBe("PATCH");
    expect(bodyOf(init)).toEqual({ name: "Enterprise accounts" });
  });

  it("reads and deletes one list by id", async () => {
    let fetchMock = stubFetch({});
    await getList(ID);
    expect(call(fetchMock)[0]).toBe(`/api/v1/engage/lists/${ID}`);

    fetchMock = stubFetch(null, 204);
    await deleteList(ID);
    const [url, init] = call(fetchMock);
    expect(url).toBe(`/api/v1/engage/lists/${ID}`);
    expect(init.method).toBe("DELETE");
  });

  it("adds members with POST and removes them with POST .../members/remove, never a DELETE body", async () => {
    let fetchMock = stubFetch({ changed: 1, unknown: [] });
    await addListMembers(ID, { contact_ids: ["c1"] });
    let [url, init] = call(fetchMock);
    expect(url).toBe(`/api/v1/engage/lists/${ID}/members`);
    expect(init.method).toBe("POST");
    expect(bodyOf(init)).toEqual({ contact_ids: ["c1"] });

    fetchMock = stubFetch({ changed: 1, unknown: [] });
    await removeListMembers(ID, { contact_ids: ["c1"] });
    [url, init] = call(fetchMock);
    expect(url).toBe(`/api/v1/engage/lists/${ID}/members/remove`);
    expect(init.method).toBe("POST");
  });

  it("pages a list's members", async () => {
    const fetchMock = stubFetch(PAGE);
    await listListMembers(ID, { page: 3, size: 10 });
    expect(call(fetchMock)[0]).toBe(`/api/v1/engage/lists/${ID}/members?page=3&size=10`);
  });
});

describe("the campaign paths", () => {
  it("filters the list by status on the server, not in the browser", async () => {
    const fetchMock = stubFetch(PAGE);
    await listCampaigns({ status: "paused", size: 100 });
    expect(call(fetchMock)[0]).toBe("/api/v1/engage/campaigns?page=1&size=100&status=paused");
  });

  it("drafts with POST, edits with PATCH and deletes with DELETE", async () => {
    let fetchMock = stubFetch({});
    await createCampaign({ name: "October", sender_id: ID });
    let [url, init] = call(fetchMock);
    expect(url).toBe("/api/v1/engage/campaigns");
    expect(init.method).toBe("POST");

    fetchMock = stubFetch({});
    await editCampaign(ID, { subject: "Hello" });
    [url, init] = call(fetchMock);
    expect(url).toBe(`/api/v1/engage/campaigns/${ID}`);
    expect(init.method).toBe("PATCH");
    expect(bodyOf(init)).toEqual({ subject: "Hello" });

    fetchMock = stubFetch(null, 204);
    await deleteCampaign(ID);
    [url, init] = call(fetchMock);
    expect(url).toBe(`/api/v1/engage/campaigns/${ID}`);
    expect(init.method).toBe("DELETE");
  });

  it("sends, cancels and resumes with POST to the action's own path, with no body", async () => {
    for (const [action, run] of [
      ["send", sendCampaign],
      ["cancel", cancelCampaign],
      ["resume", resumeCampaign],
    ] as const) {
      const fetchMock = stubFetch({});
      await run(ID);
      const [url, init] = call(fetchMock);
      expect(url).toBe(`/api/v1/engage/campaigns/${ID}/${action}`);
      expect(init.method).toBe("POST");
      expect(init.body).toBeUndefined();
    }
  });

  it("reads one campaign, and its recipients with the status filter", async () => {
    let fetchMock = stubFetch({});
    await getCampaign(ID);
    expect(call(fetchMock)[0]).toBe(`/api/v1/engage/campaigns/${ID}`);

    fetchMock = stubFetch(PAGE);
    await listCampaignRecipients(ID, { status: "failed", page: 2, size: 10 });
    expect(call(fetchMock)[0]).toBe(
      `/api/v1/engage/campaigns/${ID}/recipients?page=2&size=10&status=failed`,
    );
  });
});

describe("the fixtures", () => {
  it("carry exactly the keys the service publishes for a campaign", () => {
    for (const campaign of CAMPAIGNS) {
      expect(Object.keys(campaign).sort()).toEqual([
        "audience",
        "cancelled_at",
        "channel",
        "completed_at",
        "created_at",
        "html_body",
        "id",
        "list_id",
        "name",
        "pause_reason",
        "provider_template_id",
        "scheduled_at",
        "segment_id",
        "sender_id",
        "started_at",
        "stats",
        "status",
        "subject",
        "template_values",
        "text_body",
        "updated_at",
      ]);
      expect(Object.keys(campaign.stats).sort()).toEqual([
        "failed",
        "queued",
        "recipients",
        "sent",
        "suppressed",
      ]);
    }
  });

  it("fold recipients as the sum of the other four, as the service does", () => {
    for (const { stats } of CAMPAIGNS) {
      expect(stats.recipients).toBe(stats.queued + stats.sent + stats.failed + stats.suppressed);
    }
  });

  it("cover every campaign status the screens must render", () => {
    expect(new Set(CAMPAIGNS.map((c) => c.status))).toEqual(
      new Set(["draft", "scheduled", "sending", "paused", "sent"]),
    );
  });

  it("never pair a list audience with no list, or a list with everyone", () => {
    for (const campaign of CAMPAIGNS) {
      expect(campaign.audience === "list").toBe(campaign.list_id !== null);
    }
  });

  it("point every list-addressed campaign at a list that exists", () => {
    const ids = new Set(CONTACT_LISTS.map((l) => l.id));
    for (const campaign of CAMPAIGNS) {
      if (campaign.list_id !== null) expect(ids.has(campaign.list_id)).toBe(true);
    }
  });

  it("carry masked recipient addresses only", () => {
    for (const rows of Object.values(CAMPAIGN_RECIPIENTS)) {
      for (const row of rows) expect(row.email).toContain("•••");
    }
  });
});
