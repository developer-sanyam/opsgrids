/**
 * Every figure the console shows crosses the wire as an integer minor amount and an ISO currency,
 * and this is the only place one becomes a string — or a string becomes one. So what is pinned
 * here is the arithmetic that must never be a float, and the grouping every Indian customer would
 * see wrong at a glance.
 */

import { describe, expect, it } from "vitest";

import { formatMoney, formatMonthly, parseMinorUnits, toMajor } from "./money";

describe("toMajor", () => {
  it("moves the decimal point by the currency's own exponent", () => {
    expect(toMajor(4_500_000, "INR")).toBe(45_000);
    expect(toMajor(500, "JPY")).toBe(500);
    expect(toMajor(1_234, "KWD")).toBe(1.234);
  });

  it("assumes two places for a currency it has not been told about", () => {
    expect(toMajor(250, "XYZ")).toBe(2.5);
  });
});

describe("formatMoney", () => {
  it("groups rupees as lakh and crore, not thousands", () => {
    expect(formatMoney(45_000_000, "INR")).toBe("₹4,50,000");
    expect(formatMoney(1_234_567_800, "INR")).toBe("₹1,23,45,678");
  });

  it("drops the fraction by default, so a column of amounts scans", () => {
    expect(formatMoney(1_999_99, "INR")).toBe("₹2,000");
  });

  it("shows the currency's own fraction digits when asked for precision", () => {
    expect(formatMoney(1_999_99, "INR", { precise: true })).toBe("₹1,999.99");
    expect(formatMoney(1_234, "KWD", { precise: true })).toContain("1.234");
  });

  it("keeps the sign of a negative amount", () => {
    expect(formatMoney(-500_000, "INR")).toBe("-₹5,000");
  });

  it("goes compact for a headline figure, in lakh", () => {
    expect(formatMoney(1_840_000_00, "INR", { compact: true })).toBe("₹18L");
  });

  it("falls back to a grouped number and the code for a currency Intl does not know", () => {
    expect(formatMoney(12_345_00, "NOTACURRENCY")).toBe("12,345 NOTACURRENCY");
  });
});

describe("formatMonthly", () => {
  it("says it is per month", () => {
    expect(formatMonthly(4_500_000, "INR")).toBe("₹45,000 / mo");
  });
});

describe("parseMinorUnits", () => {
  it("turns whole rupees into paise", () => {
    expect(parseMinorUnits("45000", "INR")).toBe(4_500_000);
    expect(parseMinorUnits(" 45000 ", "INR")).toBe(4_500_000);
  });

  it("turns a fraction into minor units exactly, where a float would not", () => {
    expect(parseMinorUnits("450.5", "INR")).toBe(45_050);
    expect(parseMinorUnits("0.07", "INR")).toBe(7);
    // 1.005 * 100 is 100.49999999999999 as a float — an amount that cannot exist here.
    expect(parseMinorUnits("1.005", "INR")).toBeNull();
    expect(parseMinorUnits("1.10", "INR")).toBe(110);
  });

  it("honours the currency's exponent, so a yen has no fraction and a dinar has three", () => {
    expect(parseMinorUnits("500", "JPY")).toBe(500);
    expect(parseMinorUnits("500.5", "JPY")).toBeNull();
    expect(parseMinorUnits("1.234", "KWD")).toBe(1_234);
  });

  it("refuses anything that is not a plain unsigned amount", () => {
    for (const text of ["", " ", "abc", "-5", "+5", "12,000", "1e3", "1.", ".5", "5 000"]) {
      expect(parseMinorUnits(text, "INR"), text).toBeNull();
    }
  });

  it("refuses an amount too large to hold exactly", () => {
    expect(parseMinorUnits("99999999999999999999", "INR")).toBeNull();
  });

  it("parses zero, and leaves whether zero is allowed to the caller", () => {
    expect(parseMinorUnits("0", "INR")).toBe(0);
  });
});
