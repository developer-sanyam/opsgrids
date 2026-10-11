/**
 * Live-mode tests for the movements client: the exact method, URL, headers and JSON body the
 * gateway receives. Quantities are decimal strings on the wire, and every stock-moving write
 * carries the `Idempotency-Key` it was given.
 */
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  approveTransfer,
  cancelAdjustment,
  cancelTransfer,
  createAdjustment,
  createReservation,
  createTransfer,
  dispatchTransfer,
  getAdjustment,
  getControlSettings,
  getReservation,
  getTransfer,
  listAdjustments,
  listInTransit,
  listReservations,
  listTransfers,
  putControlSettings,
  receiveTransfer,
  rejectTransfer,
  releaseReservation,
  requestTransfer,
  updateTransfer,
} from "./movements";

function stubFetch(body: unknown, status = 200) {
  const fetchMock = vi.fn().mockResolvedValue(
    new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } }),
  );
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function requested(fetchMock: ReturnType<typeof vi.fn>): { url: string; init: RequestInit; headers: Headers } {
  const call = fetchMock.mock.calls[0] as [string, RequestInit] | undefined;
  if (!call) throw new Error("fetch was never called");
  return { url: call[0], init: call[1], headers: new Headers(call[1].headers) };
}

afterEach(() => {
  vi.unstubAllGlobals();
});

const KEY = "11111111-2222-4333-8444-555555555555";

describe("transfers", () => {
  it("lists with the filters and the cursor on the query string, and nothing that is unset", async () => {
    const fetchMock = stubFetch({ items: [], next_cursor: null });
    await listTransfers({ status: "IN_TRANSIT", q: "TRF-00", cursor: "25", limit: 25, from_warehouse_id: undefined });
    const { url, init } = requested(fetchMock);
    expect(init.method ?? "GET").toBe("GET");
    expect(url).toBe("/api/v1/operations/inventory/transfers?status=IN_TRANSIT&q=TRF-00&cursor=25&limit=25");
  });

  it("reads one transfer", async () => {
    const fetchMock = stubFetch({});
    await getTransfer("t 1");
    expect(requested(fetchMock).url).toBe("/api/v1/operations/inventory/transfers/t%201");
  });

  it("creates with an idempotency key and decimal-string quantities", async () => {
    const fetchMock = stubFetch({});
    await createTransfer(
      {
        from_warehouse_id: "a",
        to_warehouse_id: "b",
        lines: [{ inventory_item_id: "i", from_location_id: "l", quantity: "82.5" }],
      },
      KEY,
    );
    const { url, init, headers } = requested(fetchMock);
    expect(url).toBe("/api/v1/operations/inventory/transfers");
    expect(init.method).toBe("POST");
    expect(headers.get("Idempotency-Key")).toBe(KEY);
    const body = JSON.parse(String(init.body)) as { lines: { quantity: unknown }[] };
    expect(body.lines[0]?.quantity).toBe("82.5");
  });

  it("patches a draft", async () => {
    const fetchMock = stubFetch({});
    await updateTransfer("t1", { note: "hello", expected_on: null });
    const { url, init } = requested(fetchMock);
    expect(url).toBe("/api/v1/operations/inventory/transfers/t1");
    expect(init.method).toBe("PATCH");
    expect(JSON.parse(String(init.body))).toEqual({ note: "hello", expected_on: null });
  });

  it.each([
    ["request", () => requestTransfer("t1"), ":request", undefined],
    ["approve", () => approveTransfer("t1"), ":approve", {}],
    ["reject", () => rejectTransfer("t1", { comment: "no" }), ":reject", { comment: "no" }],
    ["cancel", () => cancelTransfer("t1", { reason: "changed" }), ":cancel", { reason: "changed" }],
  ])("posts %s to its own verb", async (_name, call, verb, body) => {
    const fetchMock = stubFetch({});
    await call();
    const { url, init, headers } = requested(fetchMock);
    expect(url).toBe(`/api/v1/operations/inventory/transfers/t1${verb}`);
    expect(init.method).toBe("POST");
    expect(headers.get("Idempotency-Key")).toBeNull();
    if (body === undefined) expect(init.body).toBeUndefined();
    else expect(JSON.parse(String(init.body))).toEqual(body);
  });

  it("sends the idempotency key on dispatch and receive", async () => {
    const dispatched = stubFetch({});
    await dispatchTransfer("t1", { note: "go" }, KEY);
    expect(requested(dispatched).url).toBe("/api/v1/operations/inventory/transfers/t1:dispatch");
    expect(requested(dispatched).headers.get("Idempotency-Key")).toBe(KEY);

    const received = stubFetch({});
    await receiveTransfer(
      "t1",
      { lines: [{ line_id: "l1", received_quantity: "27.5", shortage_quantity: "2.5" }] },
      KEY,
    );
    const { url, init, headers } = requested(received);
    expect(url).toBe("/api/v1/operations/inventory/transfers/t1:receive");
    expect(headers.get("Idempotency-Key")).toBe(KEY);
    expect(JSON.parse(String(init.body))).toEqual({
      lines: [{ line_id: "l1", received_quantity: "27.5", shortage_quantity: "2.5" }],
    });
  });

  it("reads the in-transit report with its filters", async () => {
    const fetchMock = stubFetch([]);
    await listInTransit({ warehouse_id: "w1" });
    expect(requested(fetchMock).url).toBe("/api/v1/operations/inventory/in-transit?warehouse_id=w1");
  });
});

describe("reservations", () => {
  it("lists and reads", async () => {
    const listed = stubFetch({ items: [], next_cursor: null });
    await listReservations({ status: "active", reference_type: "order", item_id: "i1" });
    expect(requested(listed).url).toBe(
      "/api/v1/operations/inventory/reservations?status=active&reference_type=order&item_id=i1",
    );

    const read = stubFetch({});
    await getReservation("r1");
    expect(requested(read).url).toBe("/api/v1/operations/inventory/reservations/r1");
  });

  it("creates with an idempotency key", async () => {
    const fetchMock = stubFetch({});
    await createReservation(
      { inventory_item_id: "i", location_id: "l", quantity: "12.5", reference_type: "manual" },
      KEY,
    );
    const { url, init, headers } = requested(fetchMock);
    expect(url).toBe("/api/v1/operations/inventory/reservations");
    expect(init.method).toBe("POST");
    expect(headers.get("Idempotency-Key")).toBe(KEY);
    expect((JSON.parse(String(init.body)) as { quantity: unknown }).quantity).toBe("12.5");
  });

  it("releases through the existing :release verb, whole or part", async () => {
    const whole = stubFetch({});
    await releaseReservation("r1", {}, KEY);
    expect(requested(whole).url).toBe("/api/v1/operations/inventory/reservations/r1:release");
    expect(JSON.parse(String(requested(whole).init.body))).toEqual({});
    expect(requested(whole).headers.get("Idempotency-Key")).toBe(KEY);

    const part = stubFetch({});
    await releaseReservation("r1", { quantity: "5.5" }, KEY);
    expect(JSON.parse(String(requested(part).init.body))).toEqual({ quantity: "5.5" });
  });
});

describe("adjustments", () => {
  it("creates with an idempotency key and a signed decimal string", async () => {
    const fetchMock = stubFetch({ status: "PENDING_APPROVAL" }, 202);
    const result = await createAdjustment(
      { inventory_item_id: "i", location_id: "l", quantity_delta: "-80", reason: "lost" },
      KEY,
    );
    const { url, init, headers } = requested(fetchMock);
    expect(url).toBe("/api/v1/operations/inventory/adjustments");
    expect(init.method).toBe("POST");
    expect(headers.get("Idempotency-Key")).toBe(KEY);
    expect((JSON.parse(String(init.body)) as { quantity_delta: unknown }).quantity_delta).toBe("-80");
    // A 202 is a held adjustment; the body's status says the same thing.
    expect(result.status).toBe("PENDING_APPROVAL");
  });

  it("lists, reads and cancels", async () => {
    const listed = stubFetch({ items: [], next_cursor: null });
    await listAdjustments({ status: "PENDING_APPROVAL" });
    expect(requested(listed).url).toBe("/api/v1/operations/inventory/adjustments?status=PENDING_APPROVAL");

    const read = stubFetch({});
    await getAdjustment("a1");
    expect(requested(read).url).toBe("/api/v1/operations/inventory/adjustments/a1");

    const cancelled = stubFetch({});
    await cancelAdjustment("a1", { reason: "mistake" });
    expect(requested(cancelled).url).toBe("/api/v1/operations/inventory/adjustments/a1:cancel");
    expect(requested(cancelled).init.method).toBe("POST");
    expect(JSON.parse(String(requested(cancelled).init.body))).toEqual({ reason: "mistake" });
  });
});

describe("control settings", () => {
  it("reads and replaces both thresholds", async () => {
    const read = stubFetch({});
    await getControlSettings();
    expect(requested(read).url).toBe("/api/v1/operations/inventory/control-settings");

    const put = stubFetch({});
    await putControlSettings({ transfer_approval_threshold: "500", adjustment_approval_threshold: null });
    const { url, init } = requested(put);
    expect(url).toBe("/api/v1/operations/inventory/control-settings");
    expect(init.method).toBe("PUT");
    expect(JSON.parse(String(init.body))).toEqual({
      transfer_approval_threshold: "500",
      adjustment_approval_threshold: null,
    });
  });
});
