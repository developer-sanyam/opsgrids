import { describe, expect, it } from "vitest";

import type { PublishedPlacementOut } from "@/api/types";
import {
  DEFAULT_HEIGHT_MM,
  PLAN_MARGIN_MM,
  MAX_HIGHLIGHT_IDS,
  normalizePlacements,
  parseHighlight,
  parseHighlightIds,
  placementsToSceneNodes,
  planBox,
  summarizeHighlight,
} from "./floorGeometry";

function placement(overrides: Partial<PublishedPlacementOut> = {}): PublishedPlacementOut {
  return {
    location_id: "loc-1",
    code: "A-01",
    name: "Bin A-01",
    kind: "bin",
    parent_id: null,
    x_mm: 1000,
    y_mm: 2000,
    z_mm: null,
    width_mm: 800,
    depth_mm: 600,
    height_mm: null,
    ...overrides,
  };
}

describe("normalizePlacements", () => {
  it("keeps a sound placement as a drawable rectangle", () => {
    const { drawable, skipped } = normalizePlacements([placement()]);
    expect(skipped).toEqual([]);
    expect(drawable).toEqual([
      {
        locationId: "loc-1",
        code: "A-01",
        name: "Bin A-01",
        kind: "bin",
        parentId: null,
        x: 1000,
        y: 2000,
        width: 800,
        depth: 600,
        z: null,
        height: null,
      },
    ]);
  });

  it("skips what cannot be drawn instead of throwing or drawing garbage", () => {
    const bad = [
      placement({ location_id: "zero-w", width_mm: 0 }),
      placement({ location_id: "zero-d", depth_mm: 0 }),
      placement({ location_id: "negative", width_mm: -5 }),
      placement({ location_id: "nan-x", x_mm: Number.NaN }),
      placement({ location_id: "inf-y", y_mm: Number.POSITIVE_INFINITY }),
      placement({ location_id: "nan-w", width_mm: Number.NaN }),
      placement({ location_id: "huge", x_mm: 9e12 }),
      placement({ location_id: "bad-z", z_mm: Number.NaN }),
      placement({ location_id: "zero-h", height_mm: 0 }),
      placement({ location_id: "string-x", x_mm: "12" as unknown as number }),
    ];
    const { drawable, skipped } = normalizePlacements([...bad, placement({ location_id: "good" })]);
    expect(drawable.map((r) => r.locationId)).toEqual(["good"]);
    expect(skipped.map((p) => p.location_id)).toEqual(bad.map((p) => p.location_id));
  });

  it("draws a location once even when the response repeats it", () => {
    const { drawable, skipped } = normalizePlacements([placement(), placement({ x_mm: 5 })]);
    expect(drawable).toHaveLength(1);
    expect(drawable[0]?.x).toBe(1000);
    expect(skipped).toEqual([]);
  });

  it("orders the largest footprint first so a rack sits under the bins on it", () => {
    const { drawable } = normalizePlacements([
      placement({ location_id: "bin", code: "B", width_mm: 400, depth_mm: 400 }),
      placement({ location_id: "rack", code: "R", width_mm: 4000, depth_mm: 1000 }),
    ]);
    expect(drawable.map((r) => r.locationId)).toEqual(["rack", "bin"]);
  });

  it("is empty for an empty layout", () => {
    expect(normalizePlacements([])).toEqual({ drawable: [], skipped: [] });
  });
});

describe("planBox", () => {
  it("frames every rectangle with a margin", () => {
    const { drawable } = normalizePlacements([
      placement({ location_id: "a", x_mm: 0, y_mm: 0, width_mm: 10_000, depth_mm: 5000 }),
      placement({ location_id: "b", x_mm: 12_000, y_mm: 8000, width_mm: 2000, depth_mm: 2000 }),
    ]);
    expect(planBox(drawable)).toEqual({
      minX: -PLAN_MARGIN_MM,
      minY: -PLAN_MARGIN_MM,
      width: 14_000 + PLAN_MARGIN_MM * 2,
      height: 10_000 + PLAN_MARGIN_MM * 2,
    });
  });

  it("never collapses to nothing for an empty or tiny floor", () => {
    expect(planBox([])).toEqual({ minX: 0, minY: 0, width: 4000, height: 4000 });
    const { drawable } = normalizePlacements([placement({ width_mm: 10, depth_mm: 10 })]);
    const box = planBox(drawable);
    expect(box.width).toBeGreaterThanOrEqual(4000);
    expect(box.height).toBeGreaterThanOrEqual(4000);
  });
});

describe("placementsToSceneNodes", () => {
  it("converts millimetres to metres, with plan y on the scene's Z axis and elevation on Y", () => {
    const { drawable } = normalizePlacements([
      placement({ x_mm: 1000, y_mm: 2000, z_mm: 500, width_mm: 800, depth_mm: 600, height_mm: 1200 }),
    ]);
    const [node] = placementsToSceneNodes(drawable);
    // Centre: x 1.0 + 0.4, z 2.0 + 0.3, y 0.5 + 0.6.
    expect(node?.position[0]).toBeCloseTo(1.4);
    expect(node?.position[1]).toBeCloseTo(1.1);
    expect(node?.position[2]).toBeCloseTo(2.3);
    expect(node?.size).toEqual([0.8, 1.2, 0.6]);
    expect(node?.location).toEqual({ id: "loc-1", parent_id: null, kind: "bin", code: "A-01", name: "Bin A-01" });
  });

  it("puts a placement with no elevation on the ground and gives one with no height a visible default", () => {
    const { drawable } = normalizePlacements([placement({ z_mm: null, height_mm: null })]);
    const [node] = placementsToSceneNodes(drawable);
    expect(node?.size[1]).toBeCloseTo(DEFAULT_HEIGHT_MM / 1000);
    expect(node?.position[1]).toBeCloseTo(DEFAULT_HEIGHT_MM / 2000);
  });
});

describe("parseHighlightIds", () => {
  it("splits, trims, de-duplicates and drops blanks", () => {
    expect(parseHighlightIds(" a, b ,a,, c ")).toEqual(["a", "b", "c"]);
  });

  it("is empty for absent or blank", () => {
    expect(parseHighlightIds(null)).toEqual([]);
    expect(parseHighlightIds("")).toEqual([]);
    expect(parseHighlightIds(" , ")).toEqual([]);
  });

  it("caps an absurd list rather than carrying it", () => {
    const many = Array.from({ length: 500 }, (_, i) => `id-${i}`).join(",");
    expect(parseHighlightIds(many)).toHaveLength(100);
  });
});

describe("parseHighlight", () => {
  it("keeps the first ids and reports how many the link carried, so a cap is never silent", () => {
    const many = Array.from({ length: 250 }, (_, i) => `id-${i}`).join(",");
    const parsed = parseHighlight(`${many},id-0, id-1`);
    expect(parsed.ids).toHaveLength(MAX_HIGHLIGHT_IDS);
    expect(parsed.ids[0]).toBe("id-0");
    expect(parsed.total).toBe(250);
  });

  it("reports the plain count when nothing is dropped", () => {
    expect(parseHighlight(" a, b ,a,, c ")).toEqual({ ids: ["a", "b", "c"], total: 3 });
    expect(parseHighlight(null)).toEqual({ ids: [], total: 0 });
  });
});

describe("summarizeHighlight", () => {
  it("names what was found on the plan and counts what was not", () => {
    const { drawable } = normalizePlacements([
      placement({ location_id: "a", code: "A-01" }),
      placement({ location_id: "b", code: "B-02", x_mm: 5000 }),
    ]);
    expect(summarizeHighlight(drawable, ["b", "ghost", "a"])).toEqual({ found: ["A-01", "B-02"], missing: 1 });
    expect(summarizeHighlight(drawable, [])).toEqual({ found: [], missing: 0 });
  });
});
