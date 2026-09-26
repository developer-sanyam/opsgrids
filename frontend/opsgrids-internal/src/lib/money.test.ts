import { describe, expect, it } from "vitest";

import { formatMoney, toMajor } from "./money";

describe("formatMoney", () => {
  it("formats a plain figure grouped, with no fraction digits", () => {
    expect(formatMoney(420_000_00, "INR")).toBe("₹4,20,000");
  });

  it("adds the fraction digits back for precise", () => {
    expect(formatMoney(420_000_50, "INR", { precise: true })).toBe("₹4,20,000.50");
  });

  it("compacts to lakhs with one fraction digit when the rounded figure needs it", () => {
    expect(formatMoney(1_324_200_00, "INR", { compact: true })).toBe("₹13.2L");
  });

  it("compacts with no trailing zero when the rounded figure is whole", () => {
    expect(formatMoney(97_000_00, "INR", { compact: true })).toBe("₹97K");
  });

  it("falls back to a grouped number with the code for a currency Intl doesn't know", () => {
    expect(formatMoney(100, "ZZZZ")).toBe("1 ZZZZ");
  });
});

describe("toMajor", () => {
  it("divides by the currency's minor-unit exponent", () => {
    expect(toMajor(420_000_00, "INR")).toBe(420_000);
    expect(toMajor(100, "JPY")).toBe(100);
  });
});
