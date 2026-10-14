import { describe, expect, it } from "vitest";

import { itemTitle, locationCode, locationLabel, orderLabel, qtyWithUnit } from "./display";

describe("display labels", () => {
  it("prefers the order number and falls back to the shortened id", () => {
    expect(orderLabel({ order_number: "ORD-7", order_id: "11111111-2222-3333-4444-555555555555" })).toBe("ORD-7");
    expect(orderLabel({ order_number: null, order_id: "11111111-2222-3333-4444-555555555555" })).toBe("11111111");
  });

  it("shows the bin code, with its name when it has one, else the shortened id", () => {
    expect(locationCode("A-01", "99999999-0000")).toBe("A-01");
    expect(locationCode(null, "99999999-0000")).toBe("99999999");
    expect(locationLabel("A-01", "Rack A", "x")).toBe("A-01 · Rack A");
    expect(locationLabel("A-01", null, "x")).toBe("A-01");
    expect(locationLabel(null, null, "99999999-0000")).toBe("99999999");
  });

  it("names the item by its name, else its SKU", () => {
    expect(itemTitle({ item_name: "Cotton roll", sku: "SKU-1" })).toBe("Cotton roll");
    expect(itemTitle({ item_name: null, sku: "SKU-1" })).toBe("SKU-1");
  });

  it("appends the unit only when there is one", () => {
    expect(qtyWithUnit("12.5", "m")).toBe("12.5 m");
    expect(qtyWithUnit("12.5", null)).toBe("12.5");
  });
});
