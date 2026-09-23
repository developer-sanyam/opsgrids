import { describe, expect, it } from "vitest";

import { FIXTURE_STORE } from "@/delivery/fixtures";
import { brandFromIdentity } from "@/settings/api";
import { resolveMode } from "@/theme/store";

import { getStore, getStoreBrand, listProducts, removeStore } from "./client";

/**
 * The store the fixture app opens on.
 *
 * Its own file because the seed is module state: `client.test.ts` drives `saveStore` and would
 * overwrite it, and Vitest isolates modules per file, so this one sees the seed untouched.
 */
describe("the pre-adopted fixture store", () => {
  it("opens on quickcart, under the fixture identity's own brand — not the theme's", async () => {
    const store = await getStore();

    expect(store?.themeKey).toBe("quickcart");
    // The brand lives on the store's identity, not the theme document (ADR-0011 §2): adopting
    // quickcart does not carry its suggested brand along, so this is the fixture identity's own
    // name — unrelated to which theme happens to be pre-adopted.
    expect(store && (await getStoreBrand(store.themeKey))).toEqual(
      brandFromIdentity(FIXTURE_STORE),
    );
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
