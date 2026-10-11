/**
 * The MRP client: the method, URL, JSON body and headers the gateway receives. Decimal quantities
 * must reach the wire as the strings they were given.
 */
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  createMrpRun,
  getMrpShortage,
  listMrpDemands,
  listMrpRuns,
  listMrpShortages,
  requestMrpRequisition,
} from "./client";

afterEach(() => {
  vi.unstubAllGlobals();
});

function stubFetch(body: unknown = {}, status = 200) {
  const fetchMock = vi
    .fn()
    .mockResolvedValue(new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } }));
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function sent(fetchMock: ReturnType<typeof vi.fn>) {
  const call = fetchMock.mock.calls[0] as [string, RequestInit & { headers: Record<string, string> }];
  return { url: call[0], init: call[1] };
}

const BASE = "/api/v1/operations/manufacturing/mrp";

describe("MRP client", () => {
  it("omits the status filter for All and sends it for a status", async () => {
    const all = stubFetch({ items: [], total: 0, page: 1, size: 25, pages: 0 });
    await listMrpRuns({ status: "all" });
    expect(sent(all).url).toBe(`${BASE}/runs?page=1&size=25`);

    const some = stubFetch({ items: [], total: 0, page: 1, size: 25, pages: 0 });
    await listMrpRuns({ status: "QUEUED", q: "wk" });
    expect(sent(some).url).toBe(`${BASE}/runs?page=1&size=25&status=QUEUED&q=wk`);
  });

  it("posts a run with manual quantities as strings and the Idempotency-Key", async () => {
    const fetchMock = stubFetch({});
    await createMrpRun(
      { sources: ["MANUAL"], manual_demands: [{ item_id: "i1", quantity: "82.500" }], mode: "auto" },
      { idempotencyKey: "k-1" },
    );
    const { url, init } = sent(fetchMock);
    expect(url).toBe(`${BASE}/runs`);
    expect(init.method).toBe("POST");
    expect(init.headers["Idempotency-Key"]).toBe("k-1");
    expect(JSON.parse(init.body as string)).toEqual({
      sources: ["MANUAL"],
      manual_demands: [{ item_id: "i1", quantity: "82.500" }],
      mode: "auto",
    });
    expect(init.body as string).toContain('"quantity":"82.500"');
  });

  it("filters shortages and demand on the server and escapes ids", async () => {
    const shortages = stubFetch({ items: [], total: 0, page: 1, size: 100, pages: 0 });
    await listMrpShortages("r 1", { action: "BUY", requested: false, size: 100 });
    expect(sent(shortages).url).toBe(`${BASE}/runs/r%201/shortages?page=1&size=100&action=BUY&requested=false`);

    const demands = stubFetch({ items: [], total: 0, page: 1, size: 25, pages: 0 });
    await listMrpDemands("r1", { source_type: "MANUAL" });
    expect(sent(demands).url).toBe(`${BASE}/runs/r1/demands?page=1&size=25&source_type=MANUAL`);

    const one = stubFetch({});
    await getMrpShortage("r1", "s/1");
    expect(sent(one).url).toBe(`${BASE}/runs/r1/shortages/s%2F1`);
  });

  it("requests a requisition with the Idempotency-Key", async () => {
    const fetchMock = stubFetch({});
    await requestMrpRequisition("r1", { note: "urgent" }, { idempotencyKey: "k-2" });
    const { url, init } = sent(fetchMock);
    expect(url).toBe(`${BASE}/runs/r1/requisitions`);
    expect(init.method).toBe("POST");
    expect(init.headers["Idempotency-Key"]).toBe("k-2");
    expect(JSON.parse(init.body as string)).toEqual({ note: "urgent" });
  });
});
