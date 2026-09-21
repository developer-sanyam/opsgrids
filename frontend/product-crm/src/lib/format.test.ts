import { afterEach, describe, expect, it, vi } from "vitest";

import { formatRelative, nowIso } from "./format";

describe("nowIso", () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  it("reads the real clock in live mode (VITE_USE_FIXTURES is unset in this suite)", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2030-01-01T00:00:00Z"));
    expect(nowIso()).toBe("2030-01-01T00:00:00.000Z");
  });

  it("moves as real time moves — never a constant frozen at import", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2030-01-01T00:00:00Z"));
    const first = nowIso();
    vi.setSystemTime(new Date("2030-01-02T00:00:00Z"));
    const second = nowIso();
    expect(second).not.toBe(first);
  });
});

describe("formatRelative", () => {
  it("defaults its reference instant to the real clock, not a fixture-dated constant", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2030-01-01T00:05:00Z"));
    expect(formatRelative("2030-01-01T00:00:00Z")).toBe("5 min ago");
    vi.useRealTimers();
  });

  it("returns the placeholder for a null timestamp", () => {
    expect(formatRelative(null)).toBe("—");
  });
});
