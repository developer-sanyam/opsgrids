import { describe, expect, it } from "vitest";

import { daysBetween, today } from "./format";

describe("today", () => {
  it("reads the real clock by default", () => {
    const real = new Date().toLocaleDateString("en-CA"); // local date, not UTC
    expect(today()).toBe(real);
  });

  it("takes an injected clock, for a test that needs a fixed instant", () => {
    expect(today(() => new Date("2026-08-24T11:20:00Z"))).toBe("2026-08-24");
  });
});

describe("daysBetween", () => {
  it("counts forward as positive", () => {
    expect(daysBetween("2026-08-01", "2026-08-10")).toBe(9);
  });

  it("counts backward as negative", () => {
    expect(daysBetween("2026-08-10", "2026-08-01")).toBe(-9);
  });

  it("is zero for the same date", () => {
    expect(daysBetween("2026-08-10", "2026-08-10")).toBe(0);
  });
});
