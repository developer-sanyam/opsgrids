import { describe, expect, it } from "vitest";

import {
  formatPercent,
  formatQty,
  isPctInput,
  isQtyInput,
  milliToQty,
  pctFieldError,
  qtyFieldError,
  qtyToMilli,
  stockValueMinor,
} from "./quantity";

describe("typed decimal input", () => {
  it.each(["1", "82.5", "0.125", "0", " 3 "])("accepts %j as a quantity", (raw) => {
    expect(isQtyInput(raw)).toBe(true);
  });

  it.each(["", "-1", "1.2345", "1e3", "1,5", ".5", "abc", "1."])("rejects %j as a quantity", (raw) => {
    expect(isQtyInput(raw)).toBe(false);
  });

  it("rejects zero where a quantity of work is required, and allows it where asked", () => {
    expect(qtyFieldError("0")).toBe("Enter a quantity greater than zero.");
    expect(qtyFieldError("0.000")).toBe("Enter a quantity greater than zero.");
    expect(qtyFieldError("0", { allowZero: true })).toBeUndefined();
    expect(qtyFieldError("")).toBe("Enter a quantity.");
    expect(qtyFieldError("2.5")).toBeUndefined();
  });

  it.each(["0", "2.5", "100", "100.00", "99.99", "12.34"])("accepts %j as a percentage", (raw) => {
    expect(isPctInput(raw)).toBe(true);
  });

  it.each(["", "100.01", "101", "1.234", "-1", "1e1", "1000"])("rejects %j as a percentage", (raw) => {
    expect(isPctInput(raw)).toBe(false);
  });

  it("explains a bad percentage and echoes a good one as sent", () => {
    expect(pctFieldError("")).toBe("Enter a percentage.");
    expect(pctFieldError("150")).toMatch(/0 to 100/);
    expect(pctFieldError("2.5")).toBeUndefined();
    expect(formatPercent("2.5")).toBe("2.5%");
  });
});

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

  it("values stock once, from exact milli-units", () => {
    // 82.5 m at ₹123.45/m = ₹10,184.625 → ₹10,184.63 on screen.
    expect(stockValueMinor("82.5", 123_45)).toBe(1_018_463);
    expect(stockValueMinor("6", 2_18_000_00)).toBe(13_08_000_00);
  });
});
