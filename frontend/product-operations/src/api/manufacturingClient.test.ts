/**
 * The manufacturing client: the method, URL, JSON body and headers the gateway receives. Decimal
 * quantities must reach the wire as the strings they were given.
 */
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  approveProductionOrder,
  explodeBomVersion,
  issueProductionMaterials,
  listBoms,
  listProductionOrders,
  publishBomVersion,
  recordProductionOutput,
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

describe("manufacturing client", () => {
  it("passes BOM search and paging through to the query string", async () => {
    const fetchMock = stubFetch({ items: [], total: 0, page: 2, size: 10, pages: 0 });
    await listBoms({ q: "shirt", is_active: true, page: 2, size: 10 });
    const { url } = sent(fetchMock);
    expect(url).toBe("/api/v1/operations/manufacturing/boms?page=2&size=10&q=shirt&is_active=true");
  });

  it("omits the status filter for All and sends it for a status", async () => {
    const all = stubFetch({ items: [], total: 0, page: 1, size: 25, pages: 0 });
    await listProductionOrders({ status: "all" });
    expect(sent(all).url).toBe("/api/v1/operations/manufacturing/orders?page=1&size=25");

    const some = stubFetch({ items: [], total: 0, page: 1, size: 25, pages: 0 });
    await listProductionOrders({ status: "IN_PROGRESS" });
    expect(sent(some).url).toContain("status=IN_PROGRESS");
  });

  it("sends the explosion quantity as the typed decimal string", async () => {
    const fetchMock = stubFetch({});
    await explodeBomVersion("b 1", "v1", "12.5");
    expect(sent(fetchMock).url).toBe("/api/v1/operations/manufacturing/boms/b%201/versions/v1/explosion?quantity=12.5");
  });

  it("attaches the Idempotency-Key to an order action and posts string quantities untouched", async () => {
    const fetchMock = stubFetch({});
    await issueProductionMaterials(
      "o1",
      { lines: [{ material_id: "m1", location_id: "l1", quantity: "82.5" }], over_issue: false },
      { idempotencyKey: "key-1" },
    );
    const { url, init } = sent(fetchMock);
    expect(url).toBe("/api/v1/operations/manufacturing/orders/o1/issue");
    expect(init.method).toBe("POST");
    expect(init.headers["Idempotency-Key"]).toBe("key-1");
    expect(JSON.parse(init.body as string)).toEqual({
      lines: [{ material_id: "m1", location_id: "l1", quantity: "82.5" }],
      over_issue: false,
    });
  });

  it("posts body-less actions with no body and no Content-Type", async () => {
    const fetchMock = stubFetch({});
    await approveProductionOrder("o1", { idempotencyKey: "key-2" });
    const { init } = sent(fetchMock);
    expect(init.body).toBeUndefined();
    expect(init.headers["Content-Type"]).toBeUndefined();
    expect(init.headers["Idempotency-Key"]).toBe("key-2");
  });

  it("records output at the outputs endpoint and publishes at the version's publish endpoint", async () => {
    const out = stubFetch({});
    await recordProductionOutput("o1", { quantity: "4.5", status: "AVAILABLE" });
    expect(sent(out).url).toBe("/api/v1/operations/manufacturing/orders/o1/outputs");
    expect(sent(out).init.headers["Idempotency-Key"]).toBeUndefined();

    const pub = stubFetch({});
    await publishBomVersion("b1", "v1", { idempotencyKey: "k" });
    expect(sent(pub).url).toBe("/api/v1/operations/manufacturing/boms/b1/versions/v1/publish");
    expect(sent(pub).init.headers["Idempotency-Key"]).toBe("k");
  });
});
