import { describe, expect, it } from "vitest";

import { ApiError } from "@opsgrids/client";
import { describeBlocker, describeRefusal, stockRefusals } from "./stockRefusal";

function conflict(slug: string, errors: unknown): ApiError {
  return new ApiError(409, "Not enough stock", {
    type: `https://errors.opsgrids.dev/${slug}`,
    title: "Not enough stock",
    status: 409,
    detail: "Not enough stock",
    errors: errors as never,
  });
}

const ENTRY = {
  sku: "RM-STL-4501",
  location: "BIN-A1",
  uom: "MT",
  requested: "10",
  on_hand: "12.5",
  reserved: "5",
  available: "7.5",
  reservations: [{ reservation_id: "r1", reference_type: "order", reference_id: "o1", quantity: "5" }],
};

describe("stockRefusals", () => {
  it("reads the numbers and the holds from an insufficient-stock 409", () => {
    expect(stockRefusals(conflict("insufficient-stock", [ENTRY]))).toEqual([
      {
        sku: "RM-STL-4501",
        location: "BIN-A1",
        uom: "MT",
        requested: "10",
        on_hand: "12.5",
        reserved: "5",
        available: "7.5",
        reservations: [{ reservation_id: "r1", reference_type: "order", reference_id: "o1", quantity: "5" }],
      },
    ]);
  });

  it("ignores any other conflict, and anything that is not an ApiError", () => {
    expect(stockRefusals(conflict("conflict", [ENTRY]))).toEqual([]);
    expect(stockRefusals(new Error("boom"))).toEqual([]);
  });

  it("drops an entry that does not carry the numbers", () => {
    expect(stockRefusals(conflict("insufficient-stock", [{ sku: "X" }, ENTRY]))).toHaveLength(1);
  });

  it("tolerates an entry with no reservations list", () => {
    const bare = { ...ENTRY, reservations: undefined };
    expect(stockRefusals(conflict("insufficient-stock", [bare]))[0]?.reservations).toEqual([]);
  });
});

describe("describing a refusal", () => {
  it("says requested against available in words, with the unit", () => {
    const [refusal] = stockRefusals(conflict("insufficient-stock", [ENTRY]));
    if (!refusal) throw new Error("no refusal");
    expect(describeRefusal(refusal)).toBe(
      "RM-STL-4501 at BIN-A1: you asked for 10 MT, but only 7.5 MT is available (12.5 MT on hand, 5 MT already reserved).",
    );
    const [hold] = refusal.reservations;
    if (!hold) throw new Error("no hold");
    expect(describeBlocker(hold, "MT")).toBe("5 MT reserved for an order");
  });
});
