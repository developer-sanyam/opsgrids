import { describe, expect, it } from "vitest";

import {
  grossMinor,
  gstAmountMinor,
  isIntraState,
  previewInvoice,
  roundOffMinor,
  splitTax,
  taxableMinor,
} from "./gstMath";

describe("gstMath — mirrors finance-service to the paisa", () => {
  it("rounds quantity times price once, half away from zero", () => {
    expect(grossMinor(10, 12_345)).toBe(123_450);
    expect(grossMinor(1.005, 100)).toBe(101); // 100.5 paise -> 101
    expect(grossMinor(-1.005, 100)).toBe(-101);
    expect(grossMinor(2.5, 1)).toBe(3);
  });

  it("takes a discount off the magnitude, on a return line too", () => {
    expect(taxableMinor(123_450, 5_000)).toBe(118_450);
    expect(taxableMinor(-123_450, 5_000)).toBe(-118_450);
  });

  it("computes GST once and splits it with the odd paisa to SGST", () => {
    // The server's own worked figure: 18 % of 1184.50 = 213.21 -> 106.60 + 106.61.
    const tax = gstAmountMinor(118_450, 18);
    expect(tax).toBe(21_321);
    expect(splitTax(tax, true)).toEqual({ cgst: 10_660, sgst: 10_661, igst: 0 });
    expect(splitTax(tax, false)).toEqual({ cgst: 0, sgst: 0, igst: 21_321 });
    expect(splitTax(-21_321, true)).toEqual({ cgst: -10_660, sgst: -10_661, igst: 0 });
    expect(gstAmountMinor(10_000, 0.25)).toBe(25);
    expect(gstAmountMinor(10_000, 1.5)).toBe(150);
  });

  it("treats export and SEZ as inter-state whatever the states", () => {
    expect(isIntraState("b2b", "27", "27")).toBe(true);
    expect(isIntraState("b2b", "27", "29")).toBe(false);
    expect(isIntraState("sez", "27", "27")).toBe(false);
    expect(isIntraState("b2c", null, "27")).toBe(false);
  });

  it("rounds a total to whole rupees symmetrically", () => {
    expect(roundOffMinor(139_771)).toBe(29);
    expect(roundOffMinor(123_449)).toBe(-49);
    expect(roundOffMinor(123_450)).toBe(50);
    expect(roundOffMinor(-123_450)).toBe(-50);
  });

  it("previews the document: totals by rate, round-off inside the total", () => {
    const preview = previewInvoice(
      [
        { quantity: 10, unitPriceMinor: 12_345, discountMinor: 5_000, ratePct: 18 },
        { quantity: 1, unitPriceMinor: 10_000, discountMinor: 0, ratePct: 5 },
      ],
      { intraState: true, reverseCharge: false, roundOff: true },
    );
    expect(preview.taxable).toBe(128_450);
    expect(preview.byRate.map((r) => r.ratePct)).toEqual([5, 18]);
    expect(preview.tax).toBe(21_321 + 500);
    expect(preview.total).toBe(preview.taxable + preview.tax + preview.roundOff);
    expect(preview.total % 100).toBe(0);
  });

  it("charges nothing on reverse charge", () => {
    const preview = previewInvoice([{ quantity: 1, unitPriceMinor: 10_000, discountMinor: 0, ratePct: 18 }], {
      intraState: false,
      reverseCharge: true,
      roundOff: false,
    });
    expect(preview.tax).toBe(0);
    expect(preview.total).toBe(10_000);
  });
});
