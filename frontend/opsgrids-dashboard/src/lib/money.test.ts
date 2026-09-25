import { describe, expect, it } from "vitest";

import { daysUntil, formatDate, formatMoney, formatMoneyCompact, formatMoneyKpi, formatMoneyWhole } from "./money";

describe("formatMoney", () => {
  it("divides by the currency's own minor-unit exponent, not a flat 100", () => {
    expect(formatMoney(249900, "INR")).toBe("₹2,499.00");
    expect(formatMoney(100, "JPY")).toBe("JP¥100");
  });
});

describe("formatMoneyCompact", () => {
  it("compacts to lakhs with one fraction digit when the rounded figure needs it", () => {
    expect(formatMoneyCompact(1_324_200_00, "INR")).toBe("₹13.2L");
  });
});

describe("formatMoneyKpi", () => {
  it("stays grouped for a figure short enough to fit a KPI card", () => {
    expect(formatMoneyKpi(1_248_320_00, "INR")).toBe("₹12,48,320");
  });

  it("switches to compact notation once the grouped figure risks overflowing the card", () => {
    const minor = 12_345_678_900; // ₹12,34,56,789 grouped — long enough to clip a phone KPI card.
    expect(formatMoneyWhole(minor, "INR").length).toBeGreaterThan(12);
    expect(formatMoneyKpi(minor, "INR")).toBe(formatMoneyCompact(minor, "INR"));
    expect(formatMoneyKpi(minor, "INR")).not.toBe(formatMoneyWhole(minor, "INR"));
  });
});

describe("formatDate", () => {
  it("renders a wire date for a person, and an em dash for nothing", () => {
    expect(formatDate("2026-09-20T00:00:00Z")).toBe("20 Sep 2026");
    expect(formatDate(null)).toBe("—");
    expect(formatDate("not-a-date")).toBe("—");
  });
});

describe("daysUntil", () => {
  it("counts whole days from `now` to the given instant, negative once it has passed", () => {
    const now = new Date("2026-09-20T00:00:00Z");
    expect(daysUntil("2026-09-24T00:00:00Z", now)).toBe(4);
    expect(daysUntil("2026-09-16T00:00:00Z", now)).toBe(-4);
    expect(daysUntil(null, now)).toBeNull();
  });
});
