import { describe, expect, it } from "vitest";

import { checkQuantity, checkWholeNumber, isDiscreteUom, movableMilli, sumMilli } from "./qtyInput";

describe("checkQuantity", () => {
  it("returns the exact decimal string, normalised, and its milli-units", () => {
    expect(checkQuantity("82.5", { uom: "m" })).toEqual({ ok: true, value: "82.5", milli: 82500 });
    expect(checkQuantity(" 082.500 ", { uom: "m" })).toEqual({ ok: true, value: "82.5", milli: 82500 });
    expect(checkQuantity("0.001", { uom: "kg" })).toEqual({ ok: true, value: "0.001", milli: 1 });
    expect(checkQuantity("12", { uom: "m" })).toEqual({ ok: true, value: "12", milli: 12000 });
  });

  it("never rounds or drifts: sums of decimals stay exact", () => {
    // 0.1 + 0.2 is the float trap; milli-units make it exactly 0.3.
    expect(sumMilli(["0.1", "0.2"])).toBe(300);
    expect(sumMilli(["50", "32.5"])).toBe(82500);
  });

  it("refuses an empty or non-numeric entry in words", () => {
    expect(checkQuantity("", { uom: "m" })).toEqual({ ok: false, message: "Enter a quantity." });
    for (const bad of ["abc", "1e3", "1,5", "-5", "+5", "1.", ".5", "1.2.3", "8 2"]) {
      expect(checkQuantity(bad, { uom: "m" }), bad).toEqual({
        ok: false,
        message: "Use digits and an optional decimal point, like 82.5.",
      });
    }
  });

  it("refuses more than three decimals rather than rounding them", () => {
    expect(checkQuantity("1.2345", { uom: "m" })).toEqual({
      ok: false,
      message: "Use at most 3 decimal places.",
    });
  });

  it("refuses a fraction of a discrete unit, in any case, and accepts whole ones", () => {
    for (const uom of ["pcs", "PCS", "Box", "roll", "dozen"]) {
      expect(isDiscreteUom(uom)).toBe(true);
      expect(checkQuantity("2.5", { uom })).toEqual({
        ok: false,
        message: `${uom} are counted in whole units — remove the decimals.`,
      });
      expect(checkQuantity("2", { uom })).toMatchObject({ ok: true, value: "2" });
    }
    expect(checkQuantity("2.0", { uom: "pcs" })).toMatchObject({ ok: true, value: "2" });
    expect(isDiscreteUom("m")).toBe(false);
    expect(isDiscreteUom("MT")).toBe(false);
  });

  it("refuses zero unless allowed", () => {
    expect(checkQuantity("0", { uom: "m" })).toEqual({ ok: false, message: "Quantity must be greater than zero." });
    expect(checkQuantity("0.000", { uom: "m" })).toMatchObject({ ok: false });
    expect(checkQuantity("0", { uom: "m", allowZero: true })).toEqual({ ok: true, value: "0", milli: 0 });
  });

  it("refuses more than the cap, naming it", () => {
    const rules = { uom: "m", maxMilli: 22500, maxLabel: "that can be moved" };
    expect(checkQuantity("22.5", rules)).toMatchObject({ ok: true });
    expect(checkQuantity("22.501", rules)).toEqual({
      ok: false,
      message: "That is more than the 22.5 m that can be moved.",
    });
  });

  it("refuses a quantity too large to hold exactly", () => {
    expect(checkQuantity("1000000000000", { uom: "m" })).toEqual({ ok: false, message: "That quantity is too large." });
    expect(checkQuantity("9999999999999999", { uom: "m" })).toEqual({ ok: false, message: "That quantity is too large." });
  });
});

describe("sumMilli", () => {
  it("counts an entry that is not a quantity as nothing, and allows zero", () => {
    expect(sumMilli(["10", "", "x", "0", "2.5"])).toBe(12500);
  });
});

describe("movableMilli", () => {
  it("is on hand less reserved, never negative", () => {
    expect(movableMilli({ on_hand_qty: "82.5", reserved_qty: "60" })).toBe(22500);
    expect(movableMilli({ on_hand_qty: "10", reserved_qty: "10" })).toBe(0);
    expect(movableMilli({ on_hand_qty: "5", reserved_qty: "7" })).toBe(0);
  });
});

describe("checkWholeNumber", () => {
  it("accepts a whole number in range", () => {
    expect(checkWholeNumber("150", "GSM")).toEqual({ ok: true, value: 150 });
  });
  it("refuses decimals, zero, negatives and out-of-range, naming the label", () => {
    expect(checkWholeNumber("1.5", "GSM")).toEqual({ ok: false, message: "GSM is a whole number, like 150." });
    expect(checkWholeNumber("-3", "GSM")).toMatchObject({ ok: false });
    expect(checkWholeNumber("0", "GSM")).toEqual({ ok: false, message: "GSM is between 1 and 100000." });
    expect(checkWholeNumber("100001", "Width")).toEqual({ ok: false, message: "Width is between 1 and 100000." });
  });
});
