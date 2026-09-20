import { describe, expect, it } from "vitest";

import type { SectionInstance } from "@/theme/schema";

import { bottomRow, cellFromPoint, clampRect, COLUMNS, resolveCollisions, stackOrder } from "./grid";

function section(id: string, x: number, y: number, w: number, h: number): SectionInstance {
  return { id, type: "test", settings: {}, layout: { x, y, w, h } };
}

describe("clampRect", () => {
  it("slides a section back inside the grid rather than narrowing it", () => {
    expect(clampRect({ x: 10, y: 2, w: 6, h: 3 })).toEqual({ x: 6, y: 2, w: 6, h: 3 });
  });

  it("stops a width at the full grid and a height at one row", () => {
    expect(clampRect({ x: 0, y: 0, w: 40, h: 0 })).toEqual({ x: 0, y: 0, w: COLUMNS, h: 1 });
  });

  it("never lets a section leave the top or left edge", () => {
    expect(clampRect({ x: -4, y: -9, w: 3, h: 2 })).toEqual({ x: 0, y: 0, w: 3, h: 2 });
  });

  it("resolves a non-finite value from a stored document to the floor", () => {
    expect(clampRect({ x: Number.NaN, y: 1, w: Number.NaN, h: 2 })).toEqual({
      x: 0,
      y: 1,
      w: 1,
      h: 2,
    });
  });
});

describe("resolveCollisions", () => {
  it("pushes the section under the anchor down far enough to clear it", () => {
    const sections = [section("a", 0, 0, 12, 4), section("b", 0, 2, 12, 4)];
    const settled = resolveCollisions(sections, "a");
    expect(settled[0]?.layout).toEqual({ x: 0, y: 0, w: 12, h: 4 });
    expect(settled[1]?.layout.y).toBe(4);
  });

  it("never moves the anchor itself", () => {
    const sections = [section("a", 0, 0, 12, 4), section("b", 0, 0, 12, 4)];
    expect(resolveCollisions(sections, "b")[1]?.layout.y).toBe(0);
    expect(resolveCollisions(sections, "b")[0]?.layout.y).toBe(4);
  });

  it("cascades: what a push displaces is pushed in turn", () => {
    // `a` has just been grown from two rows to four, over `b`, which sits on top of `c`.
    const settled = resolveCollisions(
      [section("a", 0, 0, 12, 4), section("b", 0, 2, 12, 2), section("c", 0, 4, 12, 2)],
      "a",
    );
    expect(settled[1]?.layout.y).toBe(4);
    expect(settled[2]?.layout.y).toBe(6);
  });

  it("leaves sections that only share rows but not columns exactly where they are", () => {
    const sections = [section("a", 0, 0, 6, 4), section("b", 6, 0, 6, 4)];
    expect(resolveCollisions(sections, "a")).toBe(sections);
  });

  it("keeps array order, which is the page order, when it moves something", () => {
    const sections = [section("a", 0, 4, 12, 2), section("b", 0, 0, 12, 6)];
    const settled = resolveCollisions(sections, "b");
    expect(settled.map((entry) => entry.id)).toEqual(["a", "b"]);
    expect(settled[0]?.layout.y).toBe(6);
  });
});

describe("stackOrder", () => {
  it("orders by row, then column, then as placed", () => {
    const sections = [section("c", 6, 2, 6, 2), section("a", 0, 0, 6, 2), section("b", 6, 0, 6, 2)];
    expect(stackOrder(sections).map((entry) => entry.id)).toEqual(["a", "b", "c"]);
  });
});

describe("bottomRow", () => {
  it("is the first row under everything on the page", () => {
    expect(bottomRow([section("a", 0, 0, 6, 3), section("b", 6, 1, 6, 5)])).toBe(6);
  });

  it("is zero for an empty page", () => {
    expect(bottomRow([])).toBe(0);
  });
});

describe("cellFromPoint", () => {
  // 12 columns of 100px with a 20px gap: 12 * 100 + 11 * 20 = 1420.
  const width = 1420;
  const gap = 20;

  it("finds the column a point sits in", () => {
    expect(cellFromPoint(0, 0, width, gap).x).toBe(0);
    expect(cellFromPoint(130, 0, width, gap).x).toBe(1);
    expect(cellFromPoint(1419, 0, width, gap).x).toBe(11);
  });

  it("clamps a point past the right edge to the last column", () => {
    expect(cellFromPoint(9000, 0, width, gap).x).toBe(COLUMNS - 1);
  });

  it("finds the row from the row height plus the gap", () => {
    expect(cellFromPoint(0, 0, width, gap).y).toBe(0);
    expect(cellFromPoint(0, 59, width, gap).y).toBe(0);
    expect(cellFromPoint(0, 61, width, gap).y).toBe(1);
  });

  it("returns the first cell when the canvas has not been measured yet", () => {
    expect(cellFromPoint(100, 100, 0, 0)).toEqual({ x: 0, y: 0 });
  });
});
