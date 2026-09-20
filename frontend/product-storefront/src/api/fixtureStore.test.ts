import { describe, expect, it } from "vitest";

import { resolveMode } from "@/theme/store";
import { SUGGESTED_BRAND } from "@/theme/themes";

import { getStore, listProducts, removeStore } from "./client";

/**
 * The store the fixture app opens on.
 *
 * Its own file because the seed is module state: `client.test.ts` drives `saveStore` and would
 * overwrite it, and Vitest isolates modules per file, so this one sees the seed untouched.
 */
describe("the pre-adopted fixture store", () => {
  it("opens on quickcart, under quickcart's own brand", async () => {
    const store = await getStore();

    expect(store?.themeKey).toBe("quickcart");
    // Not the previous seed's "Meridian Trade Depot": a grocery store under a trade depot's name
    // is the defect this pins.
    expect(store?.brand).toEqual(SUGGESTED_BRAND.quickcart);
    expect(store && resolveMode(store)).toBe("retail");
  });

  it("serves the retail catalogue that theme is written for", async () => {
    const products = await listProducts();

    expect(products.items.every((p) => p.id.startsWith("ret-"))).toBe(true);
  });

  it("still empties to no store at all", async () => {
    await removeStore();

    expect(await getStore()).toBeNull();
  });
});
