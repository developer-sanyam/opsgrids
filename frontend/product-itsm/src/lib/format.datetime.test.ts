import { afterAll, beforeAll, describe, expect, it } from "vitest";

import { formatDate, formatDateTime } from "./format";

// The assertions are about the viewer's wall clock, so the zone is pinned to the one they assert.
const originalZone = process.env.TZ;
beforeAll(() => {
  process.env.TZ = "Asia/Kolkata";
});
afterAll(() => {
  if (originalZone === undefined) delete process.env.TZ;
  else process.env.TZ = originalZone;
});

describe("formatDateTime renders an instant in the viewer's zone", () => {
  it("shows Kolkata wall-clock time with its zone name, not UTC", () => {
    const text = formatDateTime("2026-08-24T09:24:00Z");
    expect(text).toContain("2:54 pm IST");
    expect(text).toContain("24 Aug");
    expect(text).not.toContain("UTC");
  });

  it("moves to the next local day when the instant crosses midnight in the zone", () => {
    expect(formatDateTime("2026-08-24T20:00:00Z")).toContain("25 Aug");
  });

  it("returns an em dash for a missing or invalid instant", () => {
    expect(formatDateTime(null)).toBe("—");
    expect(formatDateTime("not a date")).toBe("—");
  });

  it("leaves a calendar date alone", () => {
    expect(formatDate("2026-08-24")).toBe("24 Aug 2026");
  });
});
