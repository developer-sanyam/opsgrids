/**
 * The wire calls behind the sites map, the floor view, Find stock and the put-away reveal: exact
 * method, path and body. The reveal is the one that matters most — it must be a POST with no body
 * and no query, so a coordinate can never be requested by an address that a log or a cache keeps.
 */
import { afterEach, describe, expect, it, vi } from "vitest";

import { createWarehouse, getPublishedLayout, getWarehousesMap, updateWarehouse } from "./client";
import { getPutawayDetail, locateStock, revealPutawayLocation } from "./tracking";

function stubFetch(body: unknown, status = 200) {
  // A fresh Response per call: a body can be read once.
  const fetchMock = vi.fn().mockImplementation(() =>
    Promise.resolve(new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } })),
  );
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function lastCall(fetchMock: ReturnType<typeof stubFetch>) {
  const [url, init] = fetchMock.mock.calls.at(-1) as [string, RequestInit];
  return { url, init, headers: init.headers as Record<string, string> };
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("warehouses", () => {
  it("sends the site position as integer micro-degrees on create and update", async () => {
    const fetchMock = stubFetch({ id: "wh-1", code: "WH", name: "DC", location: "", lat_micro: 19_076_090, lng_micro: 72_877_656 });
    await createWarehouse({ code: "WH", name: "DC", lat_micro: 19_076_090, lng_micro: 72_877_656 });
    let call = lastCall(fetchMock);
    expect(call.url).toBe("/api/v1/operations/warehouses");
    expect(call.init.method).toBe("POST");
    expect(JSON.parse(String(call.init.body))).toEqual({ code: "WH", name: "DC", lat_micro: 19_076_090, lng_micro: 72_877_656 });

    await updateWarehouse("wh-1", { lat_micro: null, lng_micro: null });
    call = lastCall(fetchMock);
    expect(call.url).toBe("/api/v1/operations/warehouses/wh-1");
    expect(call.init.method).toBe("PATCH");
    expect(JSON.parse(String(call.init.body))).toEqual({ lat_micro: null, lng_micro: null });
  });

  it("reads the sites map from /warehouses/map", async () => {
    const fetchMock = stubFetch({ sites: [], without_coordinates: [] });
    await expect(getWarehousesMap()).resolves.toEqual({ sites: [], without_coordinates: [] });
    const call = lastCall(fetchMock);
    expect(call.url).toBe("/api/v1/operations/warehouses/map");
    expect(call.init.method).toBe("GET");
  });
});

describe("getPublishedLayout", () => {
  it("reads the published layout of one floor, encoding the id", async () => {
    const fetchMock = stubFetch({ floor_id: "f/1", published: false, placements: [] });
    await getPublishedLayout("f/1");
    expect(lastCall(fetchMock).url).toBe("/api/v1/operations/floors/f%2F1/published-layout");
  });
});

describe("locateStock", () => {
  it("asks for one item, narrowed by whatever else is given, and leaves out what is not", async () => {
    const fetchMock = stubFetch({ results: [] });
    await locateStock({ inventory_item_id: "item-1", lot_id: "lot-1", roll_id: undefined, warehouse_id: "" });
    const call = lastCall(fetchMock);
    expect(call.url).toBe("/api/v1/operations/inventory/locate?inventory_item_id=item-1&lot_id=lot-1");
    expect(call.init.method).toBe("GET");
  });

  it("hands the abort signal to the request so a superseded search is cancelled", async () => {
    const fetchMock = stubFetch({ results: [] });
    const controller = new AbortController();
    await locateStock({ inventory_item_id: "item-1", signal: controller.signal });
    expect((fetchMock.mock.calls.at(-1)?.[1] as RequestInit).signal).toBe(controller.signal);
  });
});

describe("put-away detail and reveal", () => {
  it("reads the detail with a GET and no query", async () => {
    const fetchMock = stubFetch({ move_id: "mv-1" });
    await getPutawayDetail("mv-1");
    const call = lastCall(fetchMock);
    expect(call.url).toBe("/api/v1/operations/inventory/putaway/mv-1");
    expect(call.init.method).toBe("GET");
  });

  it("reveals with a POST: no body, no query, and nothing to cache", async () => {
    const fetchMock = stubFetch({ lat_micro: 1, lng_micro: 2, accuracy_m: 3, captured_at: "2026-10-05T00:00:00Z" });
    await revealPutawayLocation("mv-1");
    const call = lastCall(fetchMock);
    expect(call.url).toBe("/api/v1/operations/inventory/putaway/mv-1/location:reveal");
    expect(call.init.method).toBe("POST");
    expect(call.init.body).toBeUndefined();
    expect(call.url).not.toContain("?");
  });
});
