import { describe, expect, it } from "vitest";

import { daysUntil, formatSlaClock, relativeTime } from "./format";

const NOW = Date.parse("2026-08-24T18:00:00Z");

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
