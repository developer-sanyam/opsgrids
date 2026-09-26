import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "../http";
import {
  acquirePageSlot,
  collectPages,
  makeLookup,
  pagedRequest,
  resetPageBudgetForTests,
} from "./lookups";

describe("makeLookup", () => {
  const lookup = makeLookup([
    { label: "Main Warehouse", value: "w1" },
    { label: "Spare parts", value: "w2" },
    { label: "Dup", value: "a" },
    { label: "dup", value: "b" },
  ]);

  it("matches ignoring case, edges and inner spacing", () => {
    expect(lookup.resolve("  main   warehouse ")).toBe("w1");
    expect(lookup.resolve("SPARE PARTS")).toBe("w2");
  });

  it("returns undefined for an unknown label", () => {
    expect(lookup.resolve("Nowhere")).toBeUndefined();
    expect(lookup.isAmbiguous("Nowhere")).toBe(false);
  });

  it("refuses to guess between two entries with the same label", () => {
    expect(lookup.resolve("DUP")).toBeUndefined();
    expect(lookup.isAmbiguous("dup")).toBe(true);
  });

  it("does not call a label ambiguous when every entry with it gives the same value", () => {
    const same = makeLookup([
      { label: "pcs", value: "u1" },
      { label: "PCS", value: "u1" },
      { label: "Box", value: "u2" },
      { label: "box", value: "u3" },
    ]);
    expect(same.resolve("pcs")).toBe("u1");
    expect(same.isAmbiguous("pcs")).toBe(false);
    expect(same.isAmbiguous("box")).toBe(true);
  });

  it("finds the label for a value, the reverse of resolve", () => {
    expect(lookup.labelOf("w2")).toBe("Spare parts");
    expect(lookup.labelOf("nope")).toBeUndefined();
  });

  it("keeps the labels in the order given", () => {
    expect(lookup.labels).toEqual(["Main Warehouse", "Spare parts", "Dup", "dup"]);
  });
});

describe("collectPages", () => {
  const pageOf = (page: number, size: number, total: number) => {
    const start = (page - 1) * size;
    const count = Math.max(0, Math.min(size, total - start));
    const items = Array.from({ length: count }, (_, i) => start + i);
    return Promise.resolve({ items, total, pages: Math.ceil(total / size) });
  };

  it("reads every page until the server runs out", async () => {
    const rows = await collectPages((p, s) => pageOf(p, s, 450));
    expect(rows).toHaveLength(450);
    expect(rows[449]).toBe(449);
  });

  it("refuses a list longer than maxPages instead of returning part of it", async () => {
    await expect(
      collectPages((p, s) => pageOf(p, s, 10_000), { size: 100, maxPages: 3 }),
    ).rejects.toThrow(/more than 300 records/);
  });

  it("accepts a list that fills maxPages exactly", async () => {
    const rows = await collectPages((p, s) => pageOf(p, s, 300), { size: 100, maxPages: 3 });
    expect(rows).toHaveLength(300);
  });

  it("keeps a record served twice once, and reads again to find the one the server skipped", async () => {
    // Offset paging over a non-unique order: the first walk repeats id 1 and skips id 2.
    let walk = 0;
    const rows = await collectPages(
      (page, size) => {
        if (page === 1) walk += 1;
        const all = walk === 1 ? ["a", "b", "b", "d"] : ["a", "b", "c", "d"];
        const items = all.slice((page - 1) * size, page * size).map((id) => ({ id }));
        return Promise.resolve({ items, total: 4, pages: Math.ceil(4 / size) });
      },
      { size: 2, key: (r) => r.id },
    );
    expect(rows.map((r) => r.id)).toEqual(["a", "b", "c", "d"]);
    expect(walk).toBe(2);
  });

  it("refuses a list that stays short of the server's total after a second walk", async () => {
    await expect(
      collectPages(
        (page, size) => {
          const all = ["a", "a", "c"];
          const items = all.slice((page - 1) * size, page * size).map((id) => ({ id }));
          return Promise.resolve({ items, total: 3, pages: Math.ceil(3 / size) });
        },
        { size: 2, key: (r) => r.id },
      ),
    ).rejects.toThrow(/changed while it was being read/);
  });

  it("does not read twice when the list is complete", async () => {
    let calls = 0;
    await collectPages(
      (p, s) => {
        calls += 1;
        return pageOf(p, s, 5);
      },
      { size: 200, key: (n) => String(n) },
    );
    expect(calls).toBe(1);
  });

  it("never asks for more than 200 a page", async () => {
    const sizes: number[] = [];
    await collectPages(
      (p, s) => {
        sizes.push(s);
        return pageOf(p, s, 10);
      },
      { size: 5000 },
    );
    expect(sizes).toEqual([200]);
  });

  it("stops when the signal aborts", async () => {
    const controller = new AbortController();
    controller.abort();
    await expect(
      collectPages((p, s) => pageOf(p, s, 10), { signal: controller.signal }),
    ).rejects.toThrow();
  });
});

describe("paging requests against the gateway's limit", () => {
  const tooMany = () => new ApiError(429, "Too many requests", null);

  beforeEach(() => {
    resetPageBudgetForTests();
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("waits out a 429 for a minute and retries the same request", async () => {
    let calls = 0;
    const request = vi.fn(() => {
      calls += 1;
      return calls === 1 ? Promise.reject(tooMany()) : Promise.resolve("ok");
    });
    const result = pagedRequest(request);
    await vi.advanceTimersByTimeAsync(0);
    expect(request).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(59_000);
    expect(request).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(1_000);
    await expect(result).resolves.toBe("ok");
    expect(request).toHaveBeenCalledTimes(2);
  });

  it("gives up after two waits, so a stuck limit cannot hold the dialog forever", async () => {
    const request = vi.fn(() => Promise.reject(tooMany()));
    const result = pagedRequest(request);
    const settled = expect(result).rejects.toMatchObject({ status: 429 });
    await vi.advanceTimersByTimeAsync(120_000);
    await settled;
    expect(request).toHaveBeenCalledTimes(3);
  });

  it("never retries any other status", async () => {
    for (const status of [400, 403, 404, 409, 500]) {
      const request = vi.fn(() => Promise.reject(new ApiError(status, "no", null)));
      await expect(pagedRequest(request)).rejects.toMatchObject({ status });
      expect(request).toHaveBeenCalledTimes(1);
    }
  });

  it("lets a normal lookup through without waiting", async () => {
    const start = Date.now();
    for (let i = 0; i < 50; i += 1) await acquirePageSlot();
    expect(Date.now() - start).toBe(0);
  });

  it("holds a heavy caller until the rolling minute has room", async () => {
    for (let i = 0; i < 150; i += 1) await acquirePageSlot();
    let granted = false;
    const next = acquirePageSlot().then(() => {
      granted = true;
    });
    await vi.advanceTimersByTimeAsync(30_000);
    expect(granted).toBe(false);
    await vi.advanceTimersByTimeAsync(30_000);
    await next;
    expect(granted).toBe(true);
  });
});
