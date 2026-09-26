import { describe, expect, it } from "vitest";

import { daysUntil, formatMoney, formatSlaClock, relativeTime } from "./format";

const NOW = Date.parse("2026-08-24T18:00:00Z");

describe("formatMoney", () => {
  it("shows rupees and dollars as it always has: a hundredth per minor unit", () => {
    expect(formatMoney(12_500_000, "INR")).toBe("₹1,25,000");
    expect(formatMoney(2950, "INR")).toBe("₹29.50");
    expect(formatMoney(240_000_000, "USD")).toBe("$24,00,000");
  });

  it("shows a yen amount as the amount: 150000 minor units of JPY is ¥1,50,000, not ¥1,500", () => {
    expect(formatMoney(150_000, "JPY")).toBe("JP¥1,50,000");
    expect(formatMoney(150_000, "JPY")).not.toContain("1,500");
  });

  it("shows a dinar to the fil: 1250 minor units of KWD is 1.250, not 12.50", () => {
    // Intl puts a no-break space between a currency code and the amount.
    expect(formatMoney(1250, "KWD")).toBe("KWD 1.250");
    expect(formatMoney(5, "KWD")).toBe("KWD 0.005");
    expect(formatMoney(2000, "KWD")).toBe("KWD 2");
  });

  it("does not throw for a currency Intl does not know, and still scales it by the platform's table", () => {
    expect(formatMoney(1050, "ZZZ9")).toBe("ZZZ9 10.5");
  });
});

describe("relativeTime", () => {
  it("reads a fresh timestamp as just now", () => {
    expect(relativeTime("2026-08-24T17:59:45Z", NOW)).toBe("just now");
  });

  it("reads minutes ago", () => {
    expect(relativeTime("2026-08-24T17:30:00Z", NOW)).toBe("30m ago");
  });

  it("reads hours ago", () => {
    expect(relativeTime("2026-08-24T15:44:00Z", NOW)).toBe("2h ago");
  });

  it("reads days ago", () => {
    expect(relativeTime("2026-08-20T18:00:00Z", NOW)).toBe("4d ago");
  });
});

describe("formatSlaClock", () => {
  it("renders remaining time", () => {
    expect(formatSlaClock(128)).toBe("2h 08m left");
  });

  it("renders overdue time", () => {
    expect(formatSlaClock(-45)).toBe("45m over");
  });

  it("renders null as an em dash", () => {
    expect(formatSlaClock(null)).toBe("—");
  });
});

describe("daysUntil", () => {
  it("is positive for a future date", () => {
    expect(daysUntil("2026-09-01", new Date(NOW))).toBe(7);
  });

  it("is negative once a date has passed", () => {
    expect(daysUntil("2026-08-01", new Date(NOW))).toBeLessThan(0);
  });
});
