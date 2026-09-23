import { describe, expect, it } from "vitest";

import { adoptTheme as forkTheme } from "@/theme/store";
import type { StoreDoc } from "@/theme/schema";
import { depot } from "@/theme/themes";

import { listCategories, listProducts, saveStore } from "./client";

/**
 * The fixture catalogue follows the adopted store's mode.
 *
 * Driven through `saveStore` rather than `adoptTheme` on purpose: this pins the *slice* behaviour
 * on its own, so it keeps working — and keeps failing honestly — whether or not a retail theme is
 * in the library on any given day.
 */
function storeInMode(mode: StoreDoc["mode"]): StoreDoc {
  return { ...forkTheme(depot), mode };
}

describe("the fixture catalogue slice", () => {
  it("serves the trade catalogue to a trade store", async () => {
    await saveStore(storeInMode("trade"));

    const products = await listProducts();
    const categories = await listCategories();

    expect(products.items.every((p) => p.id.startsWith("prd-"))).toBe(true);
    expect(products.items.some((p) => p.code === "FST-HX-M12")).toBe(true);
    expect(categories.map((c) => c.key)).toContain("fasteners");
    expect(categories.map((c) => c.key)).not.toContain("dairy");
  });

  it("serves the retail catalogue to a retail store", async () => {
    await saveStore(storeInMode("retail"));

    const products = await listProducts();
    const categories = await listCategories();

    expect(products.items.every((p) => p.id.startsWith("ret-"))).toBe(true);
    expect(products.items.some((p) => p.name === "Amul Gold Full Cream Milk")).toBe(true);
    expect(categories.map((c) => c.key)).toContain("dairy");
    expect(categories.map((c) => c.key)).not.toContain("fasteners");
  });

  it("never lets a trade store see a delivery promise or a discount flag", async () => {
    await saveStore(storeInMode("trade"));
    const products = await listProducts();

    for (const product of products.items) {
      expect(product.delivery_eta_minutes).toBeNull();
      expect(product.mrp_minor).toBeNull();
    }
  });

  it("counts a retail category's lines from the retail catalogue alone", async () => {
    await saveStore(storeInMode("retail"));
    const [categories, products] = await Promise.all([listCategories(), listProducts()]);

    for (const category of categories) {
      const actual = products.items.filter((p) => p.category === category.key).length;
      expect(category.line_count).toBe(actual);
      expect(actual).toBeGreaterThan(0);
    }
  });
});
