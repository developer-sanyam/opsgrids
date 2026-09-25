import { describe, expect, it } from "vitest";

import { daysUntil, formatDate, formatMoney } from "./money";

describe("formatMoney", () => {
  it("divides by the currency's own minor-unit exponent, not a flat 100", () => {
    expect(formatMoney(249900, "INR")).toBe("₹2,499.00");
    expect(formatMoney(100, "JPY")).toBe("JP¥100");
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
