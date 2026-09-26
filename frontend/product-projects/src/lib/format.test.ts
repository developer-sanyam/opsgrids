import { describe, expect, it } from "vitest";

import { daysBetween, formatDate, formatMoney, relativeDue, today } from "./format";

describe("today", () => {
  it("reads the real clock as an ISO calendar date — never the frozen fixture constant", () => {
    expect(today(new Date("2026-09-21T10:00:00Z"))).toBe("2026-09-21");
  });
});

describe("formatDate", () => {
  it("renders a stored calendar date as-is, in UTC", () => {
    expect(formatDate("2026-03-15")).toBe("15 Mar 2026");
  });

  it("reads an undated milestone as an em dash, not Invalid Date", () => {
    expect(formatDate(null)).toBe("—");
  });
});

describe("relativeDue", () => {
  it("names an overdue milestone by how many days it has slipped", () => {
    // The regression the audit named: with today at 2026-09-21, a milestone due 2026-09-01 is
    // 20 days overdue — not measured against the frozen fixture "today" of 2026-08-25.
    expect(relativeDue("2026-09-01", "2026-09-21")).toBe("3w ago");
    expect(daysBetween("2026-09-01", "2026-09-21")).toBe(20);
  });

  it("reads an undated milestone as its own label, not a bogus day count", () => {
    expect(relativeDue(null, "2026-09-21")).toBe("No due date");
  });

  it("says Today for a milestone due today", () => {
    expect(relativeDue("2026-09-21", "2026-09-21")).toBe("Today");
  });
});

describe("formatMoney", () => {
  it("honours the record's own currency — never a hardcoded symbol", () => {
    expect(formatMoney(240000000, "INR")).toContain("₹");
    expect(formatMoney(240000000, "USD")).toContain("$");
    expect(formatMoney(240000000, "USD")).not.toContain("₹");
  });

  it("falls back to a plain code+amount for a currency Intl can't format", () => {
    expect(formatMoney(1050, "12")).toBe("12 10.50");
  });

  it("shows rupees as it always has: a hundredth per minor unit", () => {
    expect(formatMoney(250_000_000, "INR")).toBe("₹25,00,000");
    expect(formatMoney(2950, "INR")).toBe("₹29.50");
  });

  it("shows a yen amount as the amount: 1500000 minor units of JPY is ¥15,00,000, not ¥15,000", () => {
    expect(formatMoney(1_500_000, "JPY")).toBe("JP¥15,00,000");
    expect(formatMoney(1_500_000, "JPY")).not.toContain("15,000");
  });

  it("shows a dinar to the fil: 12345 minor units of KWD is 12.345, not 123.45", () => {
    // Intl puts a no-break space between a currency code and the amount.
    expect(formatMoney(12_345, "KWD")).toBe("KWD 12.345");
    expect(formatMoney(5, "KWD")).toBe("KWD 0.005");
    expect(formatMoney(2000, "KWD")).toBe("KWD 2");
  });
});
