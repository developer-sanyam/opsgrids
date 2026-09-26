import { describe, expect, it } from "vitest";

import { collectPages, makeLookup } from "./lookups";

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

  it("stops at maxPages", async () => {
    const rows = await collectPages((p, s) => pageOf(p, s, 10_000), { size: 100, maxPages: 3 });
    expect(rows).toHaveLength(300);
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
