import { describe, expect, it } from "vitest";

import {
  buildRequisition,
  buildRequisitionPatch,
  emptyRequisition,
  hasRequisitionErrors,
  quantityError,
  validateRequisition,
} from "./requisitionForm";
import type { RequisitionFormValues } from "./requisitionForm";

const DISCRETE = new Set(["pcs", "box"]);

function values(overrides: Partial<RequisitionFormValues["lines"][number]> = {}): RequisitionFormValues {
  const base = emptyRequisition("k1");
  return {
    ...base,
    lines: [{ key: "k1", itemId: "item-1", sku: "BOLT", name: "Bolt", uom: "pcs", quantity: "10", estPrice: "", note: "", ...overrides }],
  };
}

describe("quantityError", () => {
  it("accepts decimal strings with up to three decimals for a continuous unit", () => {
    expect(quantityError("82.5", "m", DISCRETE)).toBeUndefined();
    expect(quantityError("0.001", "m", DISCRETE)).toBeUndefined();
  });

  it("rejects empty, malformed, over-precise, and zero quantities", () => {
    expect(quantityError("", "m", DISCRETE)).toMatch(/how much/);
    expect(quantityError("abc", "m", DISCRETE)).toMatch(/three decimals/);
    expect(quantityError("1.2345", "m", DISCRETE)).toMatch(/three decimals/);
    expect(quantityError("-2", "m", DISCRETE)).toMatch(/three decimals/);
    expect(quantityError("0", "m", DISCRETE)).toMatch(/above zero/);
    expect(quantityError("0.000", "m", DISCRETE)).toMatch(/above zero/);
  });

  it("asks for a whole number for a discrete unit", () => {
    expect(quantityError("2.5", "pcs", DISCRETE)).toMatch(/whole/);
    expect(quantityError("3", "pcs", DISCRETE)).toBeUndefined();
  });
});

describe("validateRequisition", () => {
  const options = { today: "2026-10-05", discreteUoms: DISCRETE };

  it("passes a clean form", () => {
    expect(hasRequisitionErrors(validateRequisition(values(), options))).toBe(false);
  });

  it("flags a missing item, a bad quantity and a bad price on the line", () => {
    const errors = validateRequisition(values({ itemId: null, quantity: "1.5", estPrice: "12.345" }), options);
    expect(errors.lineErrors?.k1).toMatchObject({
      item: expect.stringMatching(/Choose the item/),
      quantity: expect.stringMatching(/whole/),
      estPrice: expect.any(String),
    });
  });

  it("refuses a needed-by date in the past", () => {
    const v = { ...values(), neededBy: "2026-10-01" };
    expect(validateRequisition(v, options).neededBy).toMatch(/past/);
  });

  it("needs at least one line", () => {
    const v = { ...values(), lines: [] };
    expect(validateRequisition(v, options).lines).toMatch(/at least one/);
  });
});

describe("payloads", () => {
  it("sends quantities as strings and an estimate as integer minor units", () => {
    const body = buildRequisition(
      { ...values({ quantity: "12.5", estPrice: "24.50", note: " rush " }), warehouseId: "wh-1", neededBy: "2026-10-20", reason: " restock " },
      "Asha Rao",
    );
    expect(body).toEqual({
      requested_by: "Asha Rao",
      warehouse_id: "wh-1",
      needed_by: "2026-10-20",
      reason: "restock",
      lines: [{ inventory_item_id: "item-1", quantity: "12.5", est_unit_price_minor: 2450, note: "rush" }],
    });
  });

  it("omits what was left blank on create, and nulls it on a draft edit", () => {
    expect(buildRequisition(values(), "Asha")).toEqual({
      requested_by: "Asha",
      lines: [{ inventory_item_id: "item-1", quantity: "10" }],
    });
    expect(buildRequisitionPatch(values())).toEqual({
      warehouse_id: null,
      needed_by: null,
      reason: null,
      lines: [{ inventory_item_id: "item-1", quantity: "10" }],
    });
  });
});
