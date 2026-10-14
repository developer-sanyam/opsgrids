import { describe, expect, it } from "vitest";

import { formatCalendarDate, initials } from "./format";

describe("formatCalendarDate", () => {
  it("renders a stored date as day, short month and year", () => {
    expect(formatCalendarDate("2026-03-15")).toBe("15 Mar 2026");
  });

  it("shows the stored date in any reader zone, because it renders in UTC", () => {
    expect(formatCalendarDate("2026-12-31T23:30:00Z")).toBe("31 Dec 2026");
    expect(formatCalendarDate("2027-01-01T00:00:00Z")).toBe("01 Jan 2027");
  });

  it("reads a missing date as an em dash", () => {
    expect(formatCalendarDate(null)).toBe("—");
    expect(formatCalendarDate("")).toBe("—");
  });
});

describe("initials", () => {
  it("takes the first and last word", () => {
    expect(initials("Sofia Marchetti")).toBe("SM");
    expect(initials("  ananya  b  iyer ")).toBe("AI");
  });

  it("takes one letter for a single word and none for blank", () => {
    expect(initials("Sofia")).toBe("S");
    expect(initials("   ")).toBe("");
  });
});
