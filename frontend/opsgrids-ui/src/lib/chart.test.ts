/**
 * The chart arithmetic. An axis that ends on 137, a donut whose legend adds up to 99%, or a bar
 * measured from somewhere other than zero is a chart that misleads — and each is a one-line bug
 * in a function like these.
 */

import { describe, expect, it } from "vitest";

import { arcs, areaPath, labelStride, linePath, niceStep, percentages, scaleFor, xOf, yOf } from "./chart";

describe("niceStep", () => {
  it("rounds a step up to 1, 2, 2.5 or 5 times a power of ten", () => {
    expect(niceStep(45.7)).toBe(50);
    expect(niceStep(0.17)).toBeCloseTo(0.2);
    expect(niceStep(2.2)).toBe(2.5);
    expect(niceStep(7)).toBe(10);
    expect(niceStep(1000)).toBe(1000);
  });

  it("answers 1 for a step that is not a positive number", () => {
    expect(niceStep(0)).toBe(1);
    expect(niceStep(Number.NaN)).toBe(1);
  });
});

describe("scaleFor", () => {
  it("runs from zero to a round number at or above the largest value", () => {
    expect(scaleFor([25, 60, 137])).toEqual({ min: 0, max: 150, ticks: [0, 50, 100, 150] });
  });

  it("goes below zero only as far as the most negative value needs", () => {
    expect(scaleFor([-30, 80])).toEqual({ min: -50, max: 100, ticks: [-50, 0, 50, 100] });
  });

  it("still draws an axis for a series of zeros", () => {
    expect(scaleFor([0, 0, 0]).ticks).toEqual([0, 1, 2, 3]);
  });

  it("ignores values that are not numbers rather than scaling to them", () => {
    expect(scaleFor([10, Number.NaN, 20])).toEqual({ min: 0, max: 20, ticks: [0, 10, 20] });
  });
});

describe("points", () => {
  const scale = { min: 0, max: 100, ticks: [0, 50, 100] };

  it("spreads points edge to edge and centres a lone one", () => {
    expect([xOf(0, 3), xOf(1, 3), xOf(2, 3)]).toEqual([0, 50, 100]);
    expect(xOf(0, 1)).toBe(50);
  });

  it("measures height from the top, the way SVG's y runs", () => {
    expect(yOf(100, scale)).toBe(0);
    expect(yOf(0, scale)).toBe(100);
    expect(yOf(25, scale)).toBe(75);
  });

  it("draws a line through every point and closes an area down to zero", () => {
    expect(linePath([0, 50, 100], scale)).toBe("M0.00,100.00 L50.00,50.00 L100.00,0.00");
    expect(areaPath([0, 50, 100], scale)).toBe("M0.00,100.00 L50.00,50.00 L100.00,0.00 L100.00,100.00 L0.00,100.00 Z");
    expect(areaPath([], scale)).toBe("");
  });
});

describe("arcs and percentages", () => {
  it("lays the arcs end to end, clockwise", () => {
    expect(arcs([1, 1, 2])).toEqual([
      { share: 0.25, start: 0 },
      { share: 0.25, start: 0.25 },
      { share: 0.5, start: 0.5 },
    ]);
  });

  it("counts a negative share as none and never divides by zero", () => {
    expect(arcs([-5, 5])).toEqual([
      { share: 0, start: 0 },
      { share: 1, start: 0 },
    ]);
    expect(arcs([0, 0])).toEqual([
      { share: 0, start: 0 },
      { share: 0, start: 0 },
    ]);
  });

  it("gives whole-number shares that add up to exactly 100", () => {
    const shares = percentages([112, 12, 4]);
    expect(shares).toEqual([88, 9, 3]);
    expect(percentages([1, 1, 1]).reduce((sum, share) => sum + share, 0)).toBe(100);
  });

  it("gives all zeros for a whole of nothing", () => {
    expect(percentages([0, 0])).toEqual([0, 0]);
  });
});

describe("labelStride", () => {
  it("prints every label while they fit, and every nth once they would crowd", () => {
    expect(labelStride(5)).toBe(1);
    expect(labelStride(12)).toBe(2);
    expect(labelStride(31)).toBe(6);
  });
});
