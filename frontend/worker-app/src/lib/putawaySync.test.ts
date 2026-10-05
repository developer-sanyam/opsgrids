import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@opsgrids/client";

import type { PutawayIn } from "@/api/types";
import { makePutawayOut } from "@/worker/testKit";

vi.mock("@/api/client", () => ({ postPutaway: vi.fn() }));

import { postPutaway } from "@/api/client";

import { enqueuePutaway, getPutawayQueue, parkPutaway, purgePutawayQueue, removePutaway } from "./putawayQueue";
import { classifyFailure, drainPutawayQueue } from "./putawaySync";

const post = vi.mocked(postPutaway);

const ME = { ownerSub: "user-1", organizationId: "org-1" };
const SUMMARY = { sku: "SKU-100", itemName: "Cotton roll", uom: "m", fromLocationCode: "RCV-01", toBarcode: "A-01-03" };

function enqueue(quantity: string, owner = ME) {
  const body: PutawayIn = {
    inventory_item_id: "item-1",
    from_location_id: "loc-rcv-1",
    to_barcode: "A-01-03",
    quantity,
    captured_at: "2026-09-29T09:00:00.000Z",
    occurred_on: "2026-09-29",
    lat_micro: 12_971_599,
    lng_micro: 77_594_563,
  };
  return enqueuePutaway({ ...owner, body, summary: SUMMARY });
}

function refused(status: number, detail: string): ApiError {
  return new ApiError(status, detail, { status, detail });
}

beforeEach(() => {
  post.mockReset();
});

afterEach(() => {
  purgePutawayQueue();
  localStorage.clear();
});

describe("classifyFailure", () => {
  it.each([
    [refused(401, "x"), "unauthorized"],
    [refused(404, "x"), "reject"],
    [refused(409, "x"), "reject"],
    [refused(422, "x"), "reject"],
    [refused(403, "x"), "reject"],
    [refused(408, "x"), "retry"],
    [refused(429, "x"), "retry"],
    [refused(500, "x"), "retry"],
    [refused(503, "x"), "retry"],
    [new TypeError("Failed to fetch"), "network"],
    [new DOMException("aborted", "AbortError"), "network"],
    [new SyntaxError("bad json"), "network"],
  ] as const)("%s is %s", (error, expected) => {
    expect(classifyFailure(error)).toBe(expected);
  });
});

describe("drainPutawayQueue", () => {
  it("sends each entry with its own key and frozen body, then drops it — coordinates and all", async () => {
    const first = enqueue("1");
    const second = enqueue("2");
    post.mockResolvedValue(makePutawayOut());

    const result = await drainPutawayQueue(ME);

    expect(post.mock.calls.map(([sent, key]) => [sent.quantity, key])).toEqual([
      ["1", first.idempotencyKey],
      ["2", second.idempotencyKey],
    ]);
    expect(post.mock.calls[0]?.[0]).toEqual(first.body);
    expect(result).toMatchObject({ synced: 2, stalled: false });
    expect(result.outcomes.get(first.idempotencyKey)?.kind).toBe("synced");
    expect(getPutawayQueue()).toEqual([]);
    expect(localStorage.getItem("opsgrids.worker.putawayQueue")).toBeNull();
  });

  it("a replay (the server already holds the key) is a success like any other", async () => {
    const entry = enqueue("1");
    post.mockResolvedValue(makePutawayOut({ replayed: true }));

    const result = await drainPutawayQueue(ME);

    expect(result.outcomes.get(entry.idempotencyKey)).toMatchObject({ kind: "synced", result: { replayed: true } });
    expect(getPutawayQueue()).toEqual([]);
  });

  it.each([
    [404, "That bin label isn't known."],
    [409, "That stock is reserved."],
    [422, "Put-away only moves stock out of receiving or staging."],
  ])("a %i parks the entry with the server's words and the queue carries on", async (status, detail) => {
    const refusedEntry = enqueue("1");
    const goodEntry = enqueue("2");
    post.mockRejectedValueOnce(refused(status, detail)).mockResolvedValueOnce(makePutawayOut());

    const result = await drainPutawayQueue(ME);

    expect(post).toHaveBeenCalledTimes(2);
    expect(result.synced).toBe(1);
    expect(result.stalled).toBe(false);
    const queue = getPutawayQueue();
    expect(queue).toHaveLength(1);
    expect(queue[0]).toMatchObject({
      idempotencyKey: refusedEntry.idempotencyKey,
      status: "needs_attention",
      attention: { httpStatus: status, message: detail },
    });
    expect(queue.some((entry) => entry.idempotencyKey === goodEntry.idempotencyKey)).toBe(false);
  });

  it("a parked entry has its coordinates stripped and is never sent again", async () => {
    enqueue("1");
    post.mockRejectedValueOnce(refused(409, "reserved"));
    await drainPutawayQueue(ME);

    expect(getPutawayQueue()[0]?.body).not.toHaveProperty("lat_micro");
    expect(localStorage.getItem("opsgrids.worker.putawayQueue")).not.toContain("77594563");

    post.mockReset();
    await drainPutawayQueue(ME);
    expect(post).not.toHaveBeenCalled();
  });

  it("a server error keeps the entry exactly as it was, and the next pass resends the same key and body", async () => {
    const entry = enqueue("1");
    post.mockRejectedValueOnce(refused(503, "unavailable"));

    const first = await drainPutawayQueue(ME);
    expect(first).toMatchObject({ synced: 0, stalled: true });
    expect(first.outcomes.get(entry.idempotencyKey)).toEqual({ kind: "queued", reason: "server" });
    expect(getPutawayQueue()).toEqual([entry]);

    post.mockResolvedValueOnce(makePutawayOut());
    await drainPutawayQueue(ME);

    expect(post.mock.calls[1]).toEqual([entry.body, entry.idempotencyKey, expect.anything()]);
    expect(getPutawayQueue()).toEqual([]);
  });

  it("one entry the server chokes on doesn't hold the others behind it", async () => {
    const poison = enqueue("1");
    const fine = enqueue("2");
    post.mockRejectedValueOnce(refused(500, "boom")).mockResolvedValueOnce(makePutawayOut());

    const result = await drainPutawayQueue(ME);

    expect(result).toMatchObject({ synced: 1, stalled: true });
    expect(getPutawayQueue().map((entry) => entry.idempotencyKey)).toEqual([poison.idempotencyKey]);
    expect(getPutawayQueue().some((entry) => entry.idempotencyKey === fine.idempotencyKey)).toBe(false);
  });

  it("a dropped connection stops the pass — the rest would fail the same way — and keeps everything", async () => {
    const first = enqueue("1");
    const second = enqueue("2");
    post.mockRejectedValue(new TypeError("Failed to fetch"));

    const result = await drainPutawayQueue(ME);

    expect(post).toHaveBeenCalledTimes(1);
    expect(result).toMatchObject({ synced: 0, stalled: true });
    expect(result.outcomes.get(first.idempotencyKey)).toEqual({ kind: "queued", reason: "network" });
    expect(result.outcomes.has(second.idempotencyKey)).toBe(false);
    expect(getPutawayQueue()).toHaveLength(2);
    expect(getPutawayQueue().every((entry) => entry.status === "pending")).toBe(true);
  });

  it("a lapsed session stops the pass and parks nothing: the same person syncs it after signing back in", async () => {
    const entry = enqueue("1");
    post.mockRejectedValue(refused(401, "expired"));

    const result = await drainPutawayQueue(ME);

    expect(result.outcomes.get(entry.idempotencyKey)).toEqual({ kind: "queued", reason: "session" });
    expect(getPutawayQueue()).toEqual([entry]);
  });

  it.each([408, 429])("a %i is the server asking for another go, not a refusal", async (status) => {
    const entry = enqueue("1");
    post.mockRejectedValueOnce(refused(status, "slow down"));

    await drainPutawayQueue(ME);

    expect(getPutawayQueue()).toEqual([entry]);
  });

  it("only ever sends the signed-in user's entries, in their organization", async () => {
    const mine = enqueue("1");
    enqueue("2", { ownerSub: "user-2", organizationId: "org-1" });
    enqueue("3", { ownerSub: "user-1", organizationId: "org-2" });
    post.mockResolvedValue(makePutawayOut());

    await drainPutawayQueue(ME);

    expect(post).toHaveBeenCalledTimes(1);
    expect(post.mock.calls[0]?.[1]).toBe(mine.idempotencyKey);
    expect(getPutawayQueue()).toHaveLength(2);
  });

  it("skips entries already parked", async () => {
    const entry = enqueue("1");
    parkPutaway(entry.idempotencyKey, { httpStatus: 409, message: "no" });

    const result = await drainPutawayQueue(ME);

    expect(post).not.toHaveBeenCalled();
    expect(result).toMatchObject({ synced: 0, stalled: false });
  });

  it("the entry the worker is watching is not parked: its refusal goes straight back and it leaves the queue", async () => {
    const watched = enqueue("1");
    post.mockRejectedValueOnce(refused(404, "No bin has that label."));

    const result = await drainPutawayQueue(ME, { foregroundKey: watched.idempotencyKey });

    expect(result.outcomes.get(watched.idempotencyKey)).toMatchObject({
      kind: "rejected",
      status: 404,
      message: "No bin has that label.",
    });
    expect(getPutawayQueue()).toEqual([]);
  });

  it("the entry the worker was watching is parked, not dropped, when the screen is gone by the time the refusal arrives", async () => {
    const left = enqueue("1");
    post.mockRejectedValueOnce(refused(409, "That stock is reserved."));

    const result = await drainPutawayQueue(ME, { foregroundKey: left.idempotencyKey, foregroundWatched: () => false });

    expect(getPutawayQueue()).toHaveLength(1);
    expect(getPutawayQueue()[0]).toMatchObject({
      idempotencyKey: left.idempotencyKey,
      status: "needs_attention",
      attention: { httpStatus: 409, message: "That stock is reserved." },
    });
    expect(getPutawayQueue()[0]?.body).not.toHaveProperty("lat_micro");
    expect(result.outcomes.get(left.idempotencyKey)).toMatchObject({ kind: "rejected", status: 409 });
  });

  it("whether the screen is still there is asked when the refusal arrives, not when the put-away was confirmed", async () => {
    const watched = enqueue("1");
    let stillThere = true;
    post.mockImplementationOnce(() => {
      stillThere = false; // the worker taps Home while the request is on the wire
      return Promise.reject(refused(404, "No bin has that label."));
    });

    await drainPutawayQueue(ME, { foregroundKey: watched.idempotencyKey, foregroundWatched: () => stillThere });

    expect(getPutawayQueue()[0]).toMatchObject({ idempotencyKey: watched.idempotencyKey, status: "needs_attention" });
  });

  it("an entry discarded while a pass is running is not sent: a sign-out purge reaches the entries the pass has not got to", async () => {
    enqueue("1");
    enqueue("2");
    enqueue("3");
    let answer: (() => void) | undefined;
    post.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          answer = () => resolve(makePutawayOut());
        }),
    );

    const pass = drainPutawayQueue(ME);
    await vi.waitFor(() => expect(post).toHaveBeenCalledTimes(1));
    purgePutawayQueue();
    answer?.();
    const result = await pass;

    // Only the one that was already on the wire when the worker signed out.
    expect(post).toHaveBeenCalledTimes(1);
    expect(result.synced).toBe(1);
    expect(getPutawayQueue()).toEqual([]);
  });

  it("an entry the worker discards by hand mid-pass is not sent either", async () => {
    const first = enqueue("1");
    const second = enqueue("2");
    post.mockImplementationOnce(() => {
      removePutaway(second.idempotencyKey);
      return Promise.resolve(makePutawayOut());
    });

    await drainPutawayQueue(ME);

    expect(post).toHaveBeenCalledTimes(1);
    expect(post.mock.calls[0]?.[1]).toBe(first.idempotencyKey);
  });

  it("a refusal for a date after the server's own says it is the phone's clock, not 'payload failed validation'", async () => {
    const entry = enqueue("1");
    post.mockRejectedValueOnce(
      new ApiError(422, "Request payload failed validation", {
        status: 422,
        detail: "Request payload failed validation",
        errors: [{ field: "occurred_on", code: "value_error", message: "Value error, occurred_on cannot be in the future" }],
      }),
    );

    await drainPutawayQueue(ME);

    const parked = getPutawayQueue().find((e) => e.idempotencyKey === entry.idempotencyKey);
    expect(parked?.attention?.message).toMatch(/date on this phone/i);
    expect(parked?.attention?.message).not.toMatch(/payload/i);
  });

  it("any other 422 keeps the server's own sentence", async () => {
    const entry = enqueue("1");
    post.mockRejectedValueOnce(
      new ApiError(422, "x", {
        status: 422,
        detail: "Storage put-away takes stock from a receiving or staging bin",
        errors: [{ field: "from_location_id", code: "source_not_receiving_or_staging", message: "starts from a receiving or staging bin" }],
      }),
    );

    await drainPutawayQueue(ME);

    const parked = getPutawayQueue().find((e) => e.idempotencyKey === entry.idempotencyKey);
    expect(parked?.attention?.message).toBe("Storage put-away takes stock from a receiving or staging bin");
  });

  it("a watched entry that hits a 5xx stays queued, and a different entry's refusal is still parked", async () => {
    const older = enqueue("1");
    const watched = enqueue("2");
    post.mockRejectedValueOnce(refused(409, "reserved")).mockRejectedValueOnce(refused(502, "bad gateway"));

    const result = await drainPutawayQueue(ME, { foregroundKey: watched.idempotencyKey });

    expect(result.outcomes.get(watched.idempotencyKey)).toEqual({ kind: "queued", reason: "server" });
    const byKey = new Map(getPutawayQueue().map((entry) => [entry.idempotencyKey, entry]));
    expect(byKey.get(older.idempotencyKey)?.status).toBe("needs_attention");
    expect(byKey.get(watched.idempotencyKey)?.status).toBe("pending");
  });

  it("stops and keeps everything once aborted (the app unmounting mid-pass)", async () => {
    enqueue("1");
    enqueue("2");
    const controller = new AbortController();
    post.mockImplementation(() => {
      controller.abort();
      return Promise.reject(new DOMException("aborted", "AbortError"));
    });

    const result = await drainPutawayQueue(ME, { signal: controller.signal });

    expect(post).toHaveBeenCalledTimes(1);
    expect(result.stalled).toBe(true);
    expect(getPutawayQueue()).toHaveLength(2);
  });

  it("an empty queue sends nothing", async () => {
    expect(await drainPutawayQueue(ME)).toMatchObject({ synced: 0, stalled: false });
    expect(post).not.toHaveBeenCalled();
  });
});
