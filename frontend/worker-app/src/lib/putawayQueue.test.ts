import { afterEach, describe, expect, it, vi } from "vitest";

import type { PutawayIn } from "@/api/types";

import {
  enqueuePutaway,
  getPutawayQueue,
  isOwnedBy,
  isPutawayQueuePersistent,
  parkPutaway,
  purgePutawayQueue,
  removePutaway,
  subscribePutawayQueue,
} from "./putawayQueue";

const ME = { ownerSub: "user-1", organizationId: "org-1" };

const SUMMARY = { sku: "SKU-100", itemName: "Cotton roll", uom: "m", fromLocationCode: "RCV-01", toBarcode: "A-01-03" };

function body(overrides: Partial<PutawayIn> = {}): PutawayIn {
  return {
    inventory_item_id: "item-1",
    from_location_id: "loc-rcv-1",
    to_barcode: "A-01-03",
    quantity: "5",
    captured_at: "2026-09-29T09:00:00.000Z",
    occurred_on: "2026-09-29",
    lat_micro: 12_971_599,
    lng_micro: 77_594_563,
    accuracy_m: 12,
    ...overrides,
  };
}

function enqueue(overrides: Partial<PutawayIn> = {}, owner = ME) {
  return enqueuePutaway({ ...owner, body: body(overrides), summary: SUMMARY });
}

const STORAGE_KEY = "opsgrids.worker.putawayQueue";

afterEach(() => {
  purgePutawayQueue();
  localStorage.clear();
});

describe("putawayQueue", () => {
  it("freezes the request at enqueue: a random key, the body as given, pending, and the owner", () => {
    const entry = enqueue();

    expect(entry.idempotencyKey).toMatch(/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
    expect(entry).toMatchObject({ ownerSub: "user-1", organizationId: "org-1", status: "pending", attention: null });
    expect(entry.body).toEqual(body());
    expect(Object.isFrozen(entry.body)).toBe(true);
    expect(getPutawayQueue()).toEqual([entry]);
  });

  it("never reuses a key across entries", () => {
    const keys = new Set([enqueue().idempotencyKey, enqueue().idempotencyKey, enqueue().idempotencyKey]);
    expect(keys.size).toBe(3);
  });

  it("is persisted: what is in localStorage is what a reloaded app reads back, oldest first", () => {
    const first = enqueue({ quantity: "1" });
    const second = enqueue({ quantity: "2" });

    const stored = JSON.parse(localStorage.getItem(STORAGE_KEY) ?? "null") as { idempotencyKey: string }[];
    expect(stored.map((entry) => entry.idempotencyKey)).toEqual([first.idempotencyKey, second.idempotencyKey]);
    expect(isPutawayQueuePersistent()).toBe(true);
  });

  it("hands back the same array until something changes, so a store subscriber doesn't loop", () => {
    enqueue();
    expect(getPutawayQueue()).toBe(getPutawayQueue());
  });

  it("belongs to whoever made it, in the organization they were signed in to", () => {
    const mine = enqueue();
    const theirs = enqueue({}, { ownerSub: "user-2", organizationId: "org-1" });
    const otherOrg = enqueue({}, { ownerSub: "user-1", organizationId: "org-2" });

    expect(isOwnedBy(mine, ME)).toBe(true);
    expect(isOwnedBy(theirs, ME)).toBe(false);
    expect(isOwnedBy(otherOrg, ME)).toBe(false);
  });

  it("removing an entry drops its body, coordinates and all, from storage", () => {
    const entry = enqueue();
    const keep = enqueue({ lat_micro: 1, lng_micro: 2 });

    removePutaway(entry.idempotencyKey);

    expect(getPutawayQueue().map((e) => e.idempotencyKey)).toEqual([keep.idempotencyKey]);
    expect(localStorage.getItem(STORAGE_KEY)).not.toContain("12971599");
  });

  it("removing the last entry leaves no key behind", () => {
    removePutaway(enqueue().idempotencyKey);
    expect(localStorage.getItem(STORAGE_KEY)).toBeNull();
  });

  it("parking keeps the entry for a person to read, records why, and strips the GPS fix", () => {
    const entry = enqueue();

    parkPutaway(entry.idempotencyKey, { httpStatus: 409, message: "Reserved stock can't be moved." });

    const [parked] = getPutawayQueue();
    expect(parked).toMatchObject({
      status: "needs_attention",
      attention: { httpStatus: 409, message: "Reserved stock can't be moved." },
      idempotencyKey: entry.idempotencyKey,
    });
    expect(parked?.body).not.toHaveProperty("lat_micro");
    expect(parked?.body).not.toHaveProperty("lng_micro");
    expect(parked?.body).not.toHaveProperty("accuracy_m");
    expect(parked?.body).toMatchObject({ to_barcode: "A-01-03", quantity: "5", captured_at: "2026-09-29T09:00:00.000Z" });
    const raw = localStorage.getItem(STORAGE_KEY) ?? "";
    expect(raw).not.toContain("lat_micro");
    expect(raw).not.toContain("77594563");
  });

  it("purge empties everyone's entries from storage and from memory", () => {
    enqueue();
    enqueue({}, { ownerSub: "user-2", organizationId: "org-1" });

    purgePutawayQueue();

    expect(getPutawayQueue()).toEqual([]);
    expect(localStorage.getItem(STORAGE_KEY)).toBeNull();
  });

  it("tells subscribers about every change, and stops when they unsubscribe", () => {
    const listener = vi.fn();
    const unsubscribe = subscribePutawayQueue(listener);

    const entry = enqueue();
    parkPutaway(entry.idempotencyKey, { httpStatus: 422, message: "no" });
    removePutaway(entry.idempotencyKey);
    expect(listener).toHaveBeenCalledTimes(3);

    unsubscribe();
    enqueue();
    expect(listener).toHaveBeenCalledTimes(3);
  });

  it("another tab's write reaches subscribers through the storage event", () => {
    const listener = vi.fn();
    const unsubscribe = subscribePutawayQueue(listener);

    window.dispatchEvent(new StorageEvent("storage", { key: STORAGE_KEY }));
    window.dispatchEvent(new StorageEvent("storage", { key: "something.else" }));

    expect(listener).toHaveBeenCalledTimes(1);
    unsubscribe();
  });

  it("reads what is in storage even if it was written behind the module's back", () => {
    const entry = enqueue();
    purgePutawayQueue();
    localStorage.setItem(STORAGE_KEY, JSON.stringify([entry]));

    expect(getPutawayQueue()).toHaveLength(1);
  });

  describe("corrupt storage", () => {
    it("treats unparseable JSON as an empty queue", () => {
      localStorage.setItem(STORAGE_KEY, "{not json");
      expect(getPutawayQueue()).toEqual([]);
    });

    it("treats a non-array as an empty queue", () => {
      localStorage.setItem(STORAGE_KEY, JSON.stringify({ entries: [] }));
      expect(getPutawayQueue()).toEqual([]);
    });

    it("drops entries that don't look like entries instead of ever sending them", () => {
      const good = enqueue();
      localStorage.setItem(
        STORAGE_KEY,
        JSON.stringify([good, { ownerSub: "user-1" }, "nope", null, { ...good, body: { quantity: 5 } }, { ...good, status: "weird" }]),
      );

      expect(getPutawayQueue().map((entry) => entry.idempotencyKey)).toEqual([good.idempotencyKey]);
    });
  });

  describe("localStorage unavailable", () => {
    it("keeps the queue in memory for the session and says it is not persistent", () => {
      vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
        throw new DOMException("quota", "QuotaExceededError");
      });

      const entry = enqueue();

      expect(isPutawayQueuePersistent()).toBe(false);
      expect(getPutawayQueue()).toEqual([entry]);
      parkPutaway(entry.idempotencyKey, { httpStatus: 404, message: "no such bin" });
      expect(getPutawayQueue()[0]?.status).toBe("needs_attention");
      removePutaway(entry.idempotencyKey);
      expect(getPutawayQueue()).toEqual([]);
    });

    it("survives a getItem that throws as well", () => {
      vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
        throw new Error("denied");
      });
      vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
        throw new Error("denied");
      });

      const entry = enqueue();

      expect(getPutawayQueue()).toEqual([entry]);
      expect(isPutawayQueuePersistent()).toBe(false);
    });

    it("a write that fails does not leave the older copy behind to bring a synced entry back after a reload", () => {
      const synced = enqueue();
      const waiting = enqueue({ quantity: "6" });
      expect(localStorage.getItem(STORAGE_KEY)).toContain(synced.idempotencyKey);
      vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
        throw new DOMException("quota", "QuotaExceededError");
      });

      removePutaway(synced.idempotencyKey);

      // The memory copy is the truth now, and it is right...
      expect(getPutawayQueue()).toEqual([waiting]);
      expect(isPutawayQueuePersistent()).toBe(false);
      // ...while what is on disk is the queue from before the entry synced.
      expect(localStorage.getItem(STORAGE_KEY)).toBeNull();
    });

    it("nor does it keep the GPS fix of an entry that was parked, or one that is gone, alive on disk", () => {
      const parked = enqueue();
      enqueue({ quantity: "6" });
      vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
        throw new DOMException("quota", "QuotaExceededError");
      });

      parkPutaway(parked.idempotencyKey, { httpStatus: 409, message: "Reserved stock can't be moved." });

      expect(getPutawayQueue()[0]?.body).not.toHaveProperty("lat_micro");
      expect(localStorage.getItem(STORAGE_KEY)).toBeNull();
    });

    it("tries again to clear it on every later change, when removing it failed too", () => {
      const first = enqueue();
      const second = enqueue({ quantity: "6" });
      const setItem = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
        throw new DOMException("quota", "QuotaExceededError");
      });
      const removeItem = vi.spyOn(Storage.prototype, "removeItem").mockImplementation(() => {
        throw new DOMException("denied", "SecurityError");
      });
      removePutaway(first.idempotencyKey);
      expect(localStorage.getItem(STORAGE_KEY)).toContain("12971599");

      removeItem.mockRestore();
      removePutaway(second.idempotencyKey);

      expect(localStorage.getItem(STORAGE_KEY)).toBeNull();
      expect(getPutawayQueue()).toEqual([]);
      expect(setItem).toHaveBeenCalledTimes(1);
    });

    it("purge clears memory and works again once storage does", () => {
      vi.spyOn(Storage.prototype, "setItem").mockImplementationOnce(() => {
        throw new Error("denied");
      });
      enqueue();
      expect(isPutawayQueuePersistent()).toBe(false);

      purgePutawayQueue();

      expect(getPutawayQueue()).toEqual([]);
      enqueue();
      expect(isPutawayQueuePersistent()).toBe(true);
      expect(localStorage.getItem(STORAGE_KEY)).not.toBeNull();
    });
  });
});
