/**
 * The console's dates render in UTC so a stored instant reads identically whatever the operator's
 * own timezone is, and its "relative" wording is measured from a fixed "today" the fixtures share.
 * Both are what make a screen's output something a test — or two operators on a call — can agree on.
 */

import { describe, expect, it } from "vitest";

import { daysUntil, formatDate, formatDateTime, formatInt, formatPercent, relativeDays } from "./format";

const TODAY = new Date("2026-08-24T00:00:00Z");

describe("formatDate", () => {
  it("writes an instant as day, month and year", () => {
    expect(formatDate("2026-03-15T22:30:00Z")).toBe("15 Mar 2026");
  });

  it("reads in UTC, so the operator's timezone cannot move the day", () => {
    // 23:30 UTC on the 15th is already the 16th in Kolkata; the console says the 15th.
    expect(formatDate("2026-03-15T23:30:00Z")).toBe("15 Mar 2026");
  });

  it("says nothing for a missing or unreadable date", () => {
    expect(formatDate(null)).toBe("—");
    expect(formatDate("not a date")).toBe("—");
  });
});

describe("formatDateTime", () => {
  it("adds the time, in UTC", () => {
    expect(formatDateTime("2026-03-15T16:20:00Z")).toMatch(/15 Mar 2026, 4:20\s?pm/i);
  });

  it("says nothing for a missing or unreadable instant", () => {
    expect(formatDateTime(null)).toBe("—");
    expect(formatDateTime("nope")).toBe("—");
  });
});

describe("relativeDays", () => {
  it("says today for today", () => {
    expect(relativeDays("2026-08-24T10:00:00Z", TODAY)).toBe("today");
  });

  it("counts days in both directions", () => {
    expect(relativeDays("2026-08-21T00:00:00Z", TODAY)).toBe("3 days ago");
    expect(relativeDays("2026-09-05T00:00:00Z", TODAY)).toBe("in 12 days");
    expect(relativeDays("2026-08-23T00:00:00Z", TODAY)).toBe("1 day ago");
  });

  it("switches to months once a span is long enough to be read that way", () => {
    expect(relativeDays("2026-06-01T00:00:00Z", TODAY)).toBe("3 months ago");
    expect(relativeDays("2026-11-24T00:00:00Z", TODAY)).toBe("in 3 months");
  });

  it("says nothing for a missing or unreadable date", () => {
    expect(relativeDays(null, TODAY)).toBe("—");
    expect(relativeDays("nope", TODAY)).toBe("—");
  });
});

describe("daysUntil", () => {
  it("is negative for a date already past, and null for none", () => {
    expect(daysUntil("2026-09-03T00:00:00Z", TODAY)).toBe(10);
    expect(daysUntil("2026-08-20T00:00:00Z", TODAY)).toBe(-4);
    expect(daysUntil(null, TODAY)).toBeNull();
    expect(daysUntil("nope", TODAY)).toBeNull();
  });
});

describe("numbers", () => {
  it("groups counts the Indian way", () => {
    expect(formatInt(1_234_567)).toBe("12,34,567");
  });

  it("writes a percentage to one place", () => {
    expect(formatPercent(94.24)).toBe("94.2%");
    expect(formatPercent(3.456, 2)).toBe("3.46%");
  });
});
