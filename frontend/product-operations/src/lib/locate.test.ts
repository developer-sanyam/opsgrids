import { describe, expect, it } from "vitest";

import type { LocateResultOut } from "@/api/trackingTypes";
import {
  autoArrangedHref,
  countBins,
  floorViewHref,
  groupLocateResults,
  highlightIdFor,
  highlightIdsFor,
  placementNote,
} from "./locate";

const PLACEMENT = {
  layout_version_id: "lv-1",
  x_mm: 0,
  y_mm: 0,
  z_mm: null,
  width_mm: 800,
  depth_mm: 600,
  height_mm: null,
  source: "own" as const,
  ancestor_location_id: null,
};

function result(overrides: Partial<LocateResultOut> = {}): LocateResultOut {
  return {
    warehouse_id: "wh-1",
    warehouse_name: "Bhiwandi DC",
    floor_id: "floor-1",
    floor_name: "Ground",
    location_id: "loc-1",
    code: "A-01",
    name: "Bin A-01",
    kind: "bin",
    path: "Zone A / Rack 1 / A-01",
    quantity: "12.5",
    status: "AVAILABLE",
    placement: PLACEMENT,
    placement_reason: null,
    ...overrides,
  };
}

describe("groupLocateResults", () => {
  it("groups warehouse then floor, sorted by name with the floorless last and bins by code", () => {
    const groups = groupLocateResults([
      result({ location_id: "3", code: "B-02" }),
      result({ location_id: "4", warehouse_id: "wh-0", warehouse_name: "Alpha WH", floor_id: null, floor_name: null }),
      result({ location_id: "1", code: "A-01" }),
      result({ location_id: "2", floor_id: "floor-2", floor_name: "Mezzanine", code: "M-01" }),
      result({ location_id: "5", floor_id: null, floor_name: null, code: "X-1", placement: null, placement_reason: "unplaced" }),
    ]);
    expect(groups.map((g) => g.warehouseName)).toEqual(["Alpha WH", "Bhiwandi DC"]);
    const bhiwandi = groups[1];
    expect(bhiwandi?.resultCount).toBe(4);
    expect(bhiwandi?.floors.map((f) => f.floorName)).toEqual(["Ground", "Mezzanine", null]);
    expect(bhiwandi?.floors[0]?.results.map((r) => r.code)).toEqual(["A-01", "B-02"]);
  });

  it("is empty for no results", () => {
    expect(groupLocateResults([])).toEqual([]);
  });

  it("counts a bin once when it holds stock in two statuses, since the server answers one row per bin and status", () => {
    const rows = [
      result({ location_id: "1", code: "A-01", status: "AVAILABLE" }),
      result({ location_id: "1", code: "A-01", status: "QUALITY_HOLD" }),
      result({ location_id: "2", code: "A-02" }),
    ];
    expect(groupLocateResults(rows)[0]?.resultCount).toBe(2);
    expect(countBins(rows)).toBe(2);
    expect(countBins([])).toBe(0);
  });
});

describe("highlightIdsFor", () => {
  it("lists each highlighted location once, however many rows it came from", () => {
    expect(
      highlightIdsFor([
        result({ location_id: "a", status: "AVAILABLE" }),
        result({ location_id: "a", status: "QUALITY_HOLD" }),
        result({ location_id: "b", placement: null, placement_reason: "unplaced" }),
      ]),
    ).toEqual(["a"]);
  });
});

describe("highlightIdFor", () => {
  it("is the bin itself when it is drawn, its ancestor when the geometry is the ancestor's, and null when unplaced", () => {
    expect(highlightIdFor(result())).toBe("loc-1");
    expect(
      highlightIdFor(result({ placement: { ...PLACEMENT, source: "ancestor", ancestor_location_id: "rack-9" } })),
    ).toBe("rack-9");
    expect(highlightIdFor(result({ placement: null, placement_reason: "unplaced" }))).toBeNull();
  });
});

describe("floorViewHref", () => {
  it("links the floor with every placed result highlighted, once each", () => {
    const href = floorViewHref("wh-1", "floor-1", [
      result({ location_id: "a" }),
      result({ location_id: "b" }),
      result({ location_id: "c", placement: { ...PLACEMENT, source: "ancestor", ancestor_location_id: "a" } }),
      result({ location_id: "d", placement: null, placement_reason: "unplaced" }),
    ]);
    expect(href).toBe("/warehouses/wh-1/floors/floor-1/view?highlight=a,b");
  });

  it("has no link without a floor or without anything placed", () => {
    expect(floorViewHref("wh-1", null, [result()])).toBeNull();
    expect(floorViewHref("wh-1", "floor-1", [result({ placement: null, placement_reason: "no_permission" })])).toBeNull();
  });

  it("encodes ids", () => {
    expect(floorViewHref("w/1", "f 1", [result({ location_id: "a&b" })])).toBe(
      "/warehouses/w%2F1/floors/f%201/view?highlight=a%26b",
    );
  });
});

describe("other links", () => {
  it("points a bin that is on no plan at the auto-arranged 3-D view", () => {
    expect(autoArrangedHref(result({ location_id: "loc-7", placement: null, placement_reason: "no_published_layout" }))).toBe(
      "/warehouses/wh-1/3d?highlight=loc-7",
    );
  });

  it("tells the 3-D view when the warehouse has a published layout and this bin is simply not on it", () => {
    expect(autoArrangedHref(result({ location_id: "loc-7", placement: null, placement_reason: "unplaced" }))).toBe(
      "/warehouses/wh-1/3d?highlight=loc-7&why=unplaced",
    );
  });

  it("does not repeat a floor's link for a bin's second status", () => {
    const href = floorViewHref("wh-1", "floor-1", [
      result({ location_id: "a", status: "AVAILABLE" }),
      result({ location_id: "a", status: "QUALITY_HOLD" }),
    ]);
    expect(href).toBe("/warehouses/wh-1/floors/floor-1/view?highlight=a");
  });
});

describe("placementNote", () => {
  it("says why a bin that holds stock is not on a plan", () => {
    expect(placementNote(result({ placement: null, placement_reason: "unplaced" }))).toBe("Not on floor plan");
    expect(placementNote(result({ placement: null, placement_reason: "no_published_layout" }))).toBe(
      "Floor has no published layout",
    );
    expect(placementNote(result({ placement: null, placement_reason: "no_permission" }))).toMatch(/can't read layouts/);
  });

  it("never leaves a placement-less result unexplained", () => {
    expect(placementNote(result({ placement: null, placement_reason: null }))).toBe("Not on floor plan");
  });

  it("is quiet for a bin that is drawn, and notes one shown through its ancestor", () => {
    expect(placementNote(result())).toBeNull();
    expect(
      placementNote(result({ placement: { ...PLACEMENT, source: "ancestor", ancestor_location_id: "rack-9" } })),
    ).toMatch(/rack or zone/);
  });
});
