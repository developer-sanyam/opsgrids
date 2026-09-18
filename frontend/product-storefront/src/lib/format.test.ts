import { describe, expect, it } from "vitest";

import { PRODUCTS, RETAIL_PRODUCTS } from "@/api/fixtures";
import type { Product, ProductVariant } from "@/api/types";

import { deliveryEtaLabel, discountPercent, formatMrp, variantPrice } from "./format";

/**
 * A minimal line to hang one arithmetic case on. Every test below overrides only the fields it is
 * actually about, so a new field on `Product` cannot silently change what a case is asserting.
 */
function line(overrides: Partial<Product> = {}): Product {
  return {
    id: "prd-test", code: "TST-1", name: "Test line", brand: "Test", category: "staples",
    spec: "One line of spec", unit: "per pack",
    price_min_minor: 6900, price_max_minor: 6900, currency: "INR",
    gst_inclusive: true, moq: 1, moq_unit: "pack", in_stock: true, lead_time_days: 0,
    featured: false, image_url: null, mrp_minor: null, delivery_eta_minutes: null, variants: [],
    ...overrides,
  };
}

function pack(id: string, price: number, mrp: number | null = null): ProductVariant {
  return { id, label: id, price_min_minor: price, price_max_minor: price, mrp_minor: mrp, in_stock: true };
}

describe("discountPercent", () => {
  it("is null when the merchant publishes no MRP", () => {
    expect(discountPercent(line({ mrp_minor: null }))).toBeNull();
  });

  it("is null when the MRP equals the selling price", () => {
    expect(discountPercent(line({ price_min_minor: 6900, mrp_minor: 6900 }))).toBeNull();
  });

  it("is null when the MRP is below the selling price rather than a negative discount", () => {
    expect(discountPercent(line({ price_min_minor: 6900, mrp_minor: 5900 }))).toBeNull();
  });

  it("is null when the saving floors to zero, so nothing ever prints 0% OFF", () => {
    // ₹99.50 down to ₹99.00 is 0.5% — real, but not a claim worth making.
    expect(discountPercent(line({ price_min_minor: 9900, mrp_minor: 9950 }))).toBeNull();
  });

  it("floors rather than rounds: ₹69 from ₹99 is 30% off, never 31%", () => {
    // 30/99 = 30.30%.
    expect(discountPercent(line({ price_min_minor: 6900, mrp_minor: 9900 }))).toBe(30);
  });

  it("floors a saving that would round up — 26.6% is 26% off, not 27%", () => {
    // 2660/10000 = 26.6%, which rounds to 27 and floors to 26.
    expect(discountPercent(line({ price_min_minor: 7340, mrp_minor: 10000 }))).toBe(26);
  });

  it("never claims more than the shopper actually saves, across the whole retail catalogue", () => {
    for (const product of RETAIL_PRODUCTS) {
      const claimed = discountPercent(product);
      if (claimed === null) continue;
      const mrp = product.mrp_minor ?? 0;
      const actual = ((mrp - product.price_min_minor) / mrp) * 100;
      expect(claimed).toBeLessThanOrEqual(actual);
      expect(claimed).toBeGreaterThan(0);
    }
  });
});

describe("formatMrp", () => {
  it("is null without an MRP", () => {
    expect(formatMrp(line({ mrp_minor: null }))).toBeNull();
  });

  it("is null when the MRP does not sit above the selling price", () => {
    expect(formatMrp(line({ price_min_minor: 6900, mrp_minor: 6900 }))).toBeNull();
  });

  it("prints the MRP in whole rupees", () => {
    expect(formatMrp(line({ price_min_minor: 6900, mrp_minor: 9900 }))).toBe("₹99");
  });
});

describe("deliveryEtaLabel", () => {
  it("is null when the line carries no delivery promise", () => {
    expect(deliveryEtaLabel(line({ delivery_eta_minutes: null }))).toBeNull();
  });

  it("is null rather than 0 mins for a zero or negative value off the wire", () => {
    expect(deliveryEtaLabel(line({ delivery_eta_minutes: 0 }))).toBeNull();
    expect(deliveryEtaLabel(line({ delivery_eta_minutes: -5 }))).toBeNull();
  });

  it("says minutes below the hour", () => {
    expect(deliveryEtaLabel(line({ delivery_eta_minutes: 8 }))).toBe("8 mins");
    expect(deliveryEtaLabel(line({ delivery_eta_minutes: 1 }))).toBe("1 min");
    expect(deliveryEtaLabel(line({ delivery_eta_minutes: 59 }))).toBe("59 mins");
  });

  it("crosses the hour boundary without a trailing 0 mins", () => {
    expect(deliveryEtaLabel(line({ delivery_eta_minutes: 60 }))).toBe("1 hr");
    expect(deliveryEtaLabel(line({ delivery_eta_minutes: 120 }))).toBe("2 hrs");
  });

  it("says hours and minutes together above the hour", () => {
    expect(deliveryEtaLabel(line({ delivery_eta_minutes: 70 }))).toBe("1 hr 10 mins");
    expect(deliveryEtaLabel(line({ delivery_eta_minutes: 61 }))).toBe("1 hr 1 min");
    expect(deliveryEtaLabel(line({ delivery_eta_minutes: 145 }))).toBe("2 hrs 25 mins");
  });
});

describe("variantPrice", () => {
  const withPacks = line({
    price_min_minor: 3500,
    price_max_minor: 3500,
    variants: [pack("v1", 3500), pack("v2", 6900)],
  });

  it("falls back to the product's own price when nothing is selected", () => {
    expect(variantPrice(withPacks, null)).toBe("₹35");
  });

  it("falls back when the selected id is not in the catalogue any more", () => {
    expect(variantPrice(withPacks, "v-gone")).toBe("₹35");
  });

  it("resolves the selected pack", () => {
    expect(variantPrice(withPacks, "v2")).toBe("₹69");
  });

  it("falls back to the band for a single-pack line, which is never wrong by the invariant", () => {
    const band = line({ price_min_minor: 124000, price_max_minor: 152000, variants: [] });
    expect(variantPrice(band, "v1")).toBe("₹1,240 – ₹1,520");
  });
});

describe("the fixture catalogues", () => {
  const all: Product[] = [...PRODUCTS, ...RETAIL_PRODUCTS];

  it("always carries a variants array, so no consumer needs an optional chain", () => {
    for (const product of all) expect(Array.isArray(product.variants)).toBe(true);
  });

  it("keeps the product-level price equal to the default pack's wherever variants exist", () => {
    for (const product of all) {
      const first = product.variants[0];
      if (first === undefined) continue;
      expect(product.price_min_minor).toBe(first.price_min_minor);
      expect(product.price_max_minor).toBe(first.price_max_minor);
      expect(product.mrp_minor).toBe(first.mrp_minor);
    }
  });

  it("stores whole paise, never a fractional minor unit", () => {
    for (const product of all) {
      expect(Number.isInteger(product.price_min_minor)).toBe(true);
      expect(Number.isInteger(product.price_max_minor)).toBe(true);
      if (product.mrp_minor !== null) expect(Number.isInteger(product.mrp_minor)).toBe(true);
    }
  });

  it("points every image at a bundled SVG and never at an external host", () => {
    for (const product of all) {
      if (product.image_url === null) continue;
      expect(product.image_url).toMatch(/^\/img\/[a-z0-9-]+\.svg$/);
    }
  });

  it("leaves the trade catalogue with no MRP, no delivery promise and no packs", () => {
    for (const product of PRODUCTS) {
      expect(product.mrp_minor).toBeNull();
      expect(product.delivery_eta_minutes).toBeNull();
      expect(product.variants).toHaveLength(0);
    }
  });

  it("discounts some retail lines and not others, because a store where everything is off reads fake", () => {
    const discounted = RETAIL_PRODUCTS.filter((p) => discountPercent(p) !== null).length;
    const share = discounted / RETAIL_PRODUCTS.length;
    expect(share).toBeGreaterThan(0.25);
    expect(share).toBeLessThan(0.6);
  });

  it("makes no delivery-time promise on a line that is out of stock", () => {
    for (const product of RETAIL_PRODUCTS) {
      if (product.in_stock) continue;
      expect(product.delivery_eta_minutes).toBeNull();
    }
  });
});
