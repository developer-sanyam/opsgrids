import { describe, expect, it } from "vitest";

import type { LocationOut } from "@/api/types";
import { flattenTree, indentClass, zoneSubtree } from "./locationTree";

function loc(partial: Partial<LocationOut> & Pick<LocationOut, "id" | "code">): LocationOut {
  return {
    warehouse_id: "wh-1",
    parent_id: null,
    kind: "bin",
    name: partial.code,
    is_default: false,
    barcode: null,
    pick_sequence: null,
    max_weight_grams: null,
    max_volume_milli: null,
    storage_type: null,
    ...partial,
  };
}

const ZONE = loc({ id: "zone", code: "ZONE-A", kind: "zone", pick_sequence: 1 });
const AISLE = loc({ id: "aisle", code: "A1", kind: "aisle", parent_id: "zone", pick_sequence: 1 });
const BIN_1 = loc({ id: "bin-1", code: "A1-B1", kind: "bin", parent_id: "aisle", pick_sequence: 2 });
const BIN_2 = loc({ id: "bin-2", code: "A1-B2", kind: "bin", parent_id: "aisle", pick_sequence: 1 });
const DOCK = loc({ id: "dock", code: "DOCK-1", kind: "dock", pick_sequence: 2 });

describe("flattenTree", () => {
  it("orders depth-first, each parent's children by pick_sequence then code", () => {
    const rows = flattenTree([BIN_1, DOCK, ZONE, BIN_2, AISLE]);
    expect(rows.map((r) => r.location.code)).toEqual(["ZONE-A", "A1", "A1-B2", "A1-B1", "DOCK-1"]);
    expect(rows.map((r) => r.depth)).toEqual([0, 1, 2, 2, 0]);
  });

  it("falls back to code when pick_sequence is unset, nulls sorting last", () => {
    const noSeq = loc({ id: "zone-2", code: "ZONE-B", kind: "zone", pick_sequence: null });
    const rows = flattenTree([noSeq, ZONE]);
    expect(rows.map((r) => r.location.code)).toEqual(["ZONE-A", "ZONE-B"]);
  });

  it("does not hang on a cycle in bad data", () => {
    const a = loc({ id: "a", code: "A", parent_id: "b" });
    const b = loc({ id: "b", code: "B", parent_id: "a" });
    // Neither is a root (parent_id null), so a well-formed tree visits neither — the guard is
    // for a shape that should be impossible, not for this case, but it must still terminate.
    expect(() => flattenTree([a, b])).not.toThrow();
  });
});

describe("zoneSubtree", () => {
  it("returns the zone and every descendant, and nothing outside it", () => {
    const rows = flattenTree([ZONE, AISLE, BIN_1, BIN_2, DOCK]);
    const subtree = zoneSubtree(rows, "zone");
    expect(subtree.map((l) => l.code)).toEqual(["ZONE-A", "A1", "A1-B2", "A1-B1"]);
  });

  it("is empty for a zone id that matches nothing", () => {
    const rows = flattenTree([ZONE, AISLE]);
    expect(zoneSubtree(rows, "missing")).toEqual([]);
  });
});

describe("indentClass", () => {
  it("steps the 4px scale up to depth 4, then holds", () => {
    expect(indentClass(0)).toBe("pl-0");
    expect(indentClass(1)).toBe("pl-4");
    expect(indentClass(4)).toBe("pl-16");
    expect(indentClass(9)).toBe("pl-16");
  });
});
