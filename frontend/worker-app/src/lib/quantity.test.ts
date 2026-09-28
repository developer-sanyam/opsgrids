import { describe, expect, it } from "vitest";

import { formatQty, milliToQty, qtyToMilli } from "./quantity";

describe("exact quantities", () => {
  it.each([
    ["82.5", 82_500],
    ["0.5", 500],
    ["1.25", 1_250],
    ["99999.99", 99_999_990],
    ["14.8", 14_800],
    ["0.125", 125],
    ["1", 1_000],
    ["100", 100_000],
    ["-2.3", -2_300],
  ])("%s is %i milli-units, and back", (qty, milli) => {
    expect(qtyToMilli(qty)).toBe(milli);
    expect(milliToQty(milli)).toBe(qty);
  });

  it("never drifts the way floats do", () => {
    expect(qtyToMilli("0.1") + qtyToMilli("0.2")).toBe(qtyToMilli("0.3"));
  });

  it("groups the whole part and keeps the fraction exactly as sent", () => {
    expect(formatQty("1250000.75")).toBe("12,50,000.75");
    expect(formatQty("82.5")).toBe("82.5");
    expect(formatQty("-4200")).toBe("-4,200");
  });

  it("refuses what the API would never send", () => {
    expect(() => qtyToMilli("1.2345")).toThrow();
    expect(() => qtyToMilli("1e3")).toThrow();
  });
});
