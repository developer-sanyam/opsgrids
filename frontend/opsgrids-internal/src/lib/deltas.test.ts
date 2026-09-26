import { describe, expect, it } from "vitest";

import { growthDelta, pctDelta, seriesDelta } from "./deltas";

describe("pctDelta", () => {
  it("computes a positive change", () => {
    expect(pctDelta(100, 120)).toBe(20);
  });

  it("computes a negative change", () => {
    expect(pctDelta(100, 80)).toBe(-20);
  });

  it("is null when either side is null", () => {
    expect(pctDelta(null, 80)).toBeNull();
    expect(pctDelta(100, null)).toBeNull();
    expect(pctDelta(null, null)).toBeNull();
  });

  it("is null rather than infinite when the base is zero", () => {
    expect(pctDelta(0, 40)).toBeNull();
  });
});

describe("seriesDelta", () => {
  it("compares the last two points", () => {
    expect(
      seriesDelta([
        { period: "2026-07", value: 100 },
        { period: "2026-08", value: 110 },
        { period: "2026-09", value: 121 },
      ]),
    ).toBe(10);
  });

  it("is null with fewer than two points", () => {
    expect(seriesDelta([{ period: "2026-09", value: 5 }])).toBeNull();
    expect(seriesDelta([])).toBeNull();
  });

  it("is null when the latest month has no visibility yet, never a fabricated 0%", () => {
    expect(
      seriesDelta([
        { period: "2026-08", value: 10 },
        { period: "2026-09", value: null },
      ]),
    ).toBeNull();
  });

  it("is null when the previous month predates the series, never a fabricated 0%", () => {
    expect(
      seriesDelta([
        { period: "2026-08", value: null },
        { period: "2026-09", value: 12 },
      ]),
    ).toBeNull();
  });
});

describe("growthDelta", () => {
  it("is the share of the current total added this month", () => {
    // 12 new this month, current total 128 -> previous total 116.
    expect(growthDelta([{ period: "2026-09", value: 12 }], 128)).toBeCloseTo((12 / 116) * 100, 5);
  });

  it("is null with no series", () => {
    expect(growthDelta([], 128)).toBeNull();
  });

  it("is null when the latest month has no value", () => {
    expect(growthDelta([{ period: "2026-09", value: null }], 128)).toBeNull();
  });

  it("is null when the implied previous total is not positive", () => {
    expect(growthDelta([{ period: "2026-09", value: 128 }], 128)).toBeNull();
  });
});
