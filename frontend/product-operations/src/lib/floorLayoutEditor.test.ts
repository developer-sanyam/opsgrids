import { describe, expect, it } from "vitest";

import type { LayoutGeometryOut, LocationKind, LocationOut } from "@/api/types";
import { overlappingPlacements } from "@/test-utils/floorOverlap";
import {
  ARRANGED_LABEL_FONT_MM,
  DEFAULT_FOOTPRINT_MM,
  MAX_COORDINATE_MM,
  arrangeUnplaced,
  editorStateToGeometryIn,
  euclideanDistanceMm,
  findFreeSpot,
  footprintFor,
  geometryToEditorState,
  labelFontSizeMm,
  labelWidthMm,
  locationNodeCode,
  nextGridPosition,
  nextJunctionCode,
  statesEqual,
  unplacedLocations,
} from "./floorLayoutEditor";
import type { EditorPlacement, EditorState } from "./floorLayoutEditor";

const GEOMETRY: LayoutGeometryOut = {
  version: {
    id: "v1",
    warehouse_floor_id: "f1",
    version_number: 1,
    status: "draft",
    label: "",
    published_at: null,
    published_by: null,
    created_at: "2026-09-01T00:00:00Z",
    updated_at: "2026-09-01T00:00:00Z",
  },
  placements: [
    {
      location_id: "loc-a",
      x_mm: 100,
      y_mm: 200,
      z_mm: null,
      width_mm: 1000,
      depth_mm: 1000,
      height_mm: null,
      location_code: "LOC-A",
      location_kind: "bin",
    },
  ],
  walk_nodes: [{ id: "n1", code: "NA", location_id: "loc-a", x_mm: 600, y_mm: 700, z_mm: null }],
  walk_edges: [
    {
      id: "e1",
      from_node_id: "n1",
      to_node_id: "n1",
      from_code: "NA",
      to_code: "NA",
      distance_mm: 10,
      is_traversable: true,
      bidirectional: true,
    },
  ],
};

describe("geometryToEditorState / editorStateToGeometryIn", () => {
  it("round-trips a loaded geometry into editor state and back into the wire shape", () => {
    const state = geometryToEditorState(GEOMETRY);
    expect(state.placements).toEqual([
      { locationId: "loc-a", x: 100, y: 200, z: null, width: 1000, depth: 1000, height: null },
    ]);
    expect(state.nodes).toEqual([{ code: "NA", locationId: "loc-a", x: 600, y: 700, z: null }]);
    expect(state.edges).toEqual([
      { fromCode: "NA", toCode: "NA", distanceMm: 10, isTraversable: true, bidirectional: true },
    ]);

    const geometryIn = editorStateToGeometryIn(state);
    expect(geometryIn.placements).toEqual([
      { location_id: "loc-a", x_mm: 100, y_mm: 200, z_mm: null, width_mm: 1000, depth_mm: 1000, height_mm: null },
    ]);
    expect(geometryIn.walk_nodes).toEqual([{ code: "NA", location_id: "loc-a", x_mm: 600, y_mm: 700, z_mm: null }]);
    expect(geometryIn.walk_edges).toEqual([
      { from_code: "NA", to_code: "NA", distance_mm: 10, is_traversable: true, bidirectional: true },
    ]);
  });

  it("rounds fractional coordinates to whole millimetres going out", () => {
    const state: EditorState = {
      placements: [{ locationId: "loc-a", x: 100.4, y: 200.6, z: null, width: 1000, depth: 1000, height: null }],
      nodes: [],
      edges: [],
    };
    const geometryIn = editorStateToGeometryIn(state);
    expect(geometryIn.placements[0]?.x_mm).toBe(100);
    expect(geometryIn.placements[0]?.y_mm).toBe(201);
  });
});

describe("statesEqual", () => {
  const base: EditorState = {
    placements: [{ locationId: "a", x: 0, y: 0, z: null, width: 1000, depth: 1000, height: null }],
    nodes: [{ code: "NA", locationId: "a", x: 500, y: 500, z: null }],
    edges: [{ fromCode: "NA", toCode: "NB", distanceMm: 100, isTraversable: true, bidirectional: true }],
  };

  it("is true for the same state, and for two states equal only after sorting", () => {
    expect(statesEqual(base, base)).toBe(true);
    const reordered: EditorState = {
      ...base,
      placements: [...base.placements],
      nodes: [...base.nodes].reverse(),
    };
    expect(statesEqual(base, reordered)).toBe(true);
  });

  it("is false once a placement moves", () => {
    const moved: EditorState = {
      ...base,
      placements: [{ ...base.placements[0]!, x: 50 }],
    };
    expect(statesEqual(base, moved)).toBe(false);
  });

  it("is false once an edge's distance changes", () => {
    const changed: EditorState = {
      ...base,
      edges: [{ ...base.edges[0]!, distanceMm: 999 }],
    };
    expect(statesEqual(base, changed)).toBe(false);
  });
});

describe("nextGridPosition", () => {
  it("packs left to right, top to bottom, six per row", () => {
    expect(nextGridPosition(0)).toEqual({ x: 0, y: 0 });
    expect(nextGridPosition(5)).toEqual({ x: 5 * 1500, y: 0 });
    expect(nextGridPosition(6)).toEqual({ x: 0, y: 1500 });
    expect(nextGridPosition(7)).toEqual({ x: 1500, y: 1500 });
  });
});

describe("locationNodeCode", () => {
  it("is stable for the same location id", () => {
    expect(locationNodeCode("11111111-2222-3333-4444-555555555555")).toBe(
      locationNodeCode("11111111-2222-3333-4444-555555555555"),
    );
  });

  it("differs for different locations", () => {
    expect(locationNodeCode("aaaa")).not.toBe(locationNodeCode("bbbb"));
  });
});

describe("nextJunctionCode", () => {
  it("starts at J-1 and skips codes already in use", () => {
    expect(nextJunctionCode(new Set())).toBe("J-1");
    expect(nextJunctionCode(new Set(["J-1"]))).toBe("J-2");
    expect(nextJunctionCode(new Set(["J-1", "J-2", "J-4"]))).toBe("J-3");
  });
});

describe("unplacedLocations", () => {
  it("excludes locations already placed in this draft", () => {
    const state: EditorState = {
      placements: [{ locationId: "a", x: 0, y: 0, z: null, width: 1000, depth: 1000, height: null }],
      nodes: [],
      edges: [],
    };
    const locations = [
      { id: "a", warehouse_id: "w", parent_id: null, kind: "bin" as const, code: "A", name: "A", is_default: false, barcode: null, pick_sequence: null, max_weight_grams: null, max_volume_milli: null, storage_type: null },
      { id: "b", warehouse_id: "w", parent_id: null, kind: "bin" as const, code: "B", name: "B", is_default: false, barcode: null, pick_sequence: null, max_weight_grams: null, max_volume_milli: null, storage_type: null },
    ];
    expect(unplacedLocations(locations, state).map((l) => l.id)).toEqual(["b"]);
  });
});

describe("euclideanDistanceMm", () => {
  it("computes and rounds the straight-line distance", () => {
    expect(euclideanDistanceMm({ x: 0, y: 0 }, { x: 3000, y: 4000 })).toBe(5000);
  });
});

describe("DEFAULT_FOOTPRINT_MM", () => {
  it("matches the server's own default (LocationPlacementIn.width_mm/depth_mm)", () => {
    expect(DEFAULT_FOOTPRINT_MM).toBe(1000);
  });
});

function loc(
  id: string,
  kind: LocationKind,
  code: string,
  parentId: string | null = null,
  pickSequence: number | null = null,
): LocationOut {
  return {
    id,
    warehouse_id: "w",
    parent_id: parentId,
    kind,
    code,
    name: code,
    is_default: false,
    barcode: null,
    pick_sequence: pickSequence,
    max_weight_grams: null,
    max_volume_milli: null,
    storage_type: null,
  };
}

function boxesOf(placements: readonly EditorPlacement[]) {
  return placements.map((p) => ({
    location_id: p.locationId,
    x_mm: p.x,
    y_mm: p.y,
    width_mm: p.width,
    depth_mm: p.depth,
  }));
}

function placement(locationId: string, x: number, y: number, width = 1000, depth = 600): EditorPlacement {
  return { locationId, x, y, z: null, width, depth, height: null };
}

function byId(placements: readonly EditorPlacement[], id: string): EditorPlacement {
  const found = placements.find((p) => p.locationId === id);
  if (!found) throw new Error(`no placement for ${id}`);
  return found;
}

/** The owner's floor: two zones, a rack with two shelves, two bins on the first shelf. */
const FLOOR_ZERO: LocationOut[] = [
  loc("z1", "zone", "Z1"),
  loc("z2", "zone", "Z2"),
  loc("r1", "rack", "Z1-R1", "z1"),
  loc("s1", "shelf", "Z1-R1-S1", "r1"),
  loc("b1", "bin", "Z1-R1-S1-B1", "s1"),
  loc("b2", "bin", "Z1-R1-S1-B2", "s1"),
  loc("s2", "shelf", "Z1-R1-S2", "r1"),
];

describe("footprintFor / labelWidthMm / labelFontSizeMm", () => {
  it("sizes a location by its kind", () => {
    expect(footprintFor("rack", "R1")).toEqual({ width: 2400, depth: 1000 });
    expect(footprintFor("bin", "B1")).toEqual({ width: 1000, depth: 600 });
    expect(footprintFor("zone", "Z1")).toEqual({ width: 4000, depth: 800 });
  });

  it("widens a tile whose code would not fit, on a 100mm grid", () => {
    const { width } = footprintFor("bin", "Z1-R1-S1-B1");
    expect(width).toBe(1900);
    expect(width % 100).toBe(0);
    expect(width).toBe(labelWidthMm("Z1-R1-S1-B1"));
  });

  it("gives every kind a footprint that carries its label at the arranged size", () => {
    const kinds: LocationKind[] = [
      "zone", "aisle", "rack", "shelf", "bin", "staging", "pack_station", "dock", "receiving", "returns", "quarantine",
    ];
    for (const kind of kinds) {
      for (const code of ["A", "B-12", "Z1-R1-S1-B1", "WAREHOUSE-ZONE-A-RACK-04-SHELF-02-BIN-09"]) {
        const { width, depth } = footprintFor(kind, code);
        expect(labelFontSizeMm(width, depth, code)).toBeGreaterThanOrEqual(ARRANGED_LABEL_FONT_MM);
      }
    }
  });

  it("sizes a label from its rectangle, within a sane range", () => {
    // A hand-sized 1000x1000 tile keeps the look it always had: a quarter of the width.
    expect(labelFontSizeMm(1000, 1000, "LOC-A")).toBe(250);
    expect(labelFontSizeMm(100_000, 100_000, "A")).toBe(400);
    expect(labelFontSizeMm(200, 200, "A-VERY-LONG-CODE-THAT-CANNOT-FIT")).toBe(60);
  });
});

describe("arrangeUnplaced", () => {
  it("returns nothing for nothing", () => {
    expect(arrangeUnplaced([], [])).toEqual({ placements: [], skipped: [] });
  });

  it("places every unplaced location exactly once, on whole millimetres, with no z or height", () => {
    const { placements, skipped } = arrangeUnplaced(FLOOR_ZERO, []);
    expect(skipped).toEqual([]);
    expect(placements.map((p) => p.locationId).sort()).toEqual(FLOOR_ZERO.map((l) => l.id).sort());
    for (const p of placements) {
      expect([p.x, p.y, p.width, p.depth].every(Number.isInteger)).toBe(true);
      expect(p.z).toBeNull();
      expect(p.height).toBeNull();
    }
  });

  it("lays the owner's floor out as bands, then one row per depth, beneath each parent", () => {
    const { placements } = arrangeUnplaced(FLOOR_ZERO, []);
    // Section Z1: its band spans the widest row beneath it (the bins: 1900 + 300 + 1900).
    expect(byId(placements, "z1")).toMatchObject({ x: 500, y: 500, width: 4100, depth: 800 });
    expect(byId(placements, "r1")).toMatchObject({ x: 500, y: 1900, width: 2400, depth: 1000 });
    // Two shelves of one rack sit together; the bins of S1 sit beneath them.
    expect(byId(placements, "s1")).toMatchObject({ x: 500, y: 3500, width: 1800, depth: 600 });
    expect(byId(placements, "s2")).toMatchObject({ x: 2600, y: 3500 });
    expect(byId(placements, "b1")).toMatchObject({ x: 500, y: 4700, width: 1900, depth: 600 });
    expect(byId(placements, "b2")).toMatchObject({ x: 2700, y: 4700 });
    // Section Z2 starts below Z1's, a section gap on.
    expect(byId(placements, "z2")).toMatchObject({ x: 500, y: 6500, width: 4000, depth: 800 });
  });

  it("never overlaps, under the server's rule, and stays inside the canvas", () => {
    const { placements } = arrangeUnplaced(FLOOR_ZERO, []);
    expect(overlappingPlacements(boxesOf(placements))).toEqual([]);
    for (const p of placements) {
      expect(p.x).toBeGreaterThanOrEqual(0);
      expect(p.y).toBeGreaterThanOrEqual(0);
      expect(p.x + p.width).toBeLessThanOrEqual(MAX_COORDINATE_MM);
      expect(p.y + p.depth).toBeLessThanOrEqual(MAX_COORDINATE_MM);
    }
  });

  it("is deterministic, and does not depend on the order the locations arrive in", () => {
    const first = arrangeUnplaced(FLOOR_ZERO, []);
    expect(arrangeUnplaced(FLOOR_ZERO, [])).toEqual(first);
    expect(arrangeUnplaced([...FLOOR_ZERO].reverse(), [])).toEqual(first);
  });

  it("orders siblings by pick sequence (nulls last), then code", () => {
    const shelf = loc("s", "shelf", "S");
    const bins = [
      loc("late", "bin", "A-LATE", "s"),
      loc("second", "bin", "X-SECOND", "s", 2),
      loc("first", "bin", "B-FIRST", "s", 1),
      loc("later", "bin", "B-LATER", "s"),
    ];
    const { placements } = arrangeUnplaced([shelf, ...bins], []);
    const order = ["first", "second", "late", "later"];
    const xs = order.map((id) => byId(placements, id).x);
    expect(xs).toEqual([...xs].sort((a, b) => a - b));
    expect(new Set(xs).size).toBe(4);
  });

  it("puts a wider aisle between two parents' groups than between siblings", () => {
    const locations = [
      loc("z", "zone", "Z"),
      loc("ra", "rack", "R-A", "z"),
      loc("rb", "rack", "R-B", "z"),
      loc("sa1", "shelf", "S-A1", "ra"),
      loc("sa2", "shelf", "S-A2", "ra"),
      loc("sb1", "shelf", "S-B1", "rb"),
    ];
    const { placements } = arrangeUnplaced(locations, []);
    const [a1, a2, b1] = ["sa1", "sa2", "sb1"].map((id) => byId(placements, id));
    if (!a1 || !a2 || !b1) throw new Error("missing placement");
    expect(a2.x - (a1.x + a1.width)).toBe(300);
    expect(b1.x - (a2.x + a2.width)).toBe(900);
    expect(overlappingPlacements(boxesOf(placements))).toEqual([]);
  });

  it("gathers roots with nothing beneath them into one row instead of a band each", () => {
    const locations = [loc("d", "dock", "DOCK"), loc("s", "staging", "STAGE"), loc("b", "bin", "LOOSE")];
    const { placements } = arrangeUnplaced(locations, []);
    expect(new Set(placements.map((p) => p.y)).size).toBe(1);
    // By code: DOCK, LOOSE, STAGE.
    expect(placements.map((p) => p.locationId)).toEqual(["d", "b", "s"]);
  });

  it("starts below whatever is already on the floor, aligned with its left edge", () => {
    const existing = [placement("old-1", 200, 100, 1000, 800), placement("old-2", 2000, 3000, 1500, 1000)];
    const { placements } = arrangeUnplaced(FLOOR_ZERO, existing);
    for (const p of placements) {
      expect(p.y).toBeGreaterThanOrEqual(4000 + 1200);
      expect(p.x).toBeGreaterThanOrEqual(200);
    }
    expect(byId(placements, "z1")).toMatchObject({ x: 200, y: 5200 });
    expect(overlappingPlacements(boxesOf([...existing, ...placements]))).toEqual([]);
  });

  it("treats a location whose parent is already placed as the root of its own group", () => {
    const orphans = [loc("b", "bin", "Z1-R1-S9-B1", "placed-parent"), loc("c", "bin", "Z1-R1-S9-B2", "placed-parent")];
    const { placements, skipped } = arrangeUnplaced(orphans, [placement("placed-parent", 500, 500)]);
    expect(placements).toHaveLength(2);
    expect(skipped).toEqual([]);
  });

  it("still places locations a parent cycle hides from the tree", () => {
    const cyclic = [loc("a", "shelf", "A", "b"), loc("b", "shelf", "B", "a"), loc("c", "bin", "C")];
    const { placements } = arrangeUnplaced(cyclic, []);
    expect(placements.map((p) => p.locationId).sort()).toEqual(["a", "b", "c"]);
    expect(overlappingPlacements(boxesOf(placements))).toEqual([]);
  });

  it("keeps a few hundred bins roughly square rather than one long strip", () => {
    const rack = loc("rack", "rack", "RACK");
    const bins = Array.from({ length: 400 }, (_, i) => loc(`bin-${i}`, "bin", `BIN-${String(i).padStart(4, "0")}`, "rack"));
    const { placements, skipped } = arrangeUnplaced([rack, ...bins], []);
    expect(skipped).toEqual([]);
    expect(placements).toHaveLength(401);
    expect(overlappingPlacements(boxesOf(placements))).toEqual([]);
    const width = Math.max(...placements.map((p) => p.x + p.width));
    const height = Math.max(...placements.map((p) => p.y + p.depth));
    expect(width / height).toBeGreaterThan(0.5);
    expect(width / height).toBeLessThan(2);
  });

  it("leaves out, and reports, what would pass the server's coordinate bound", () => {
    const { placements, skipped } = arrangeUnplaced(FLOOR_ZERO, [placement("old", 0, MAX_COORDINATE_MM - 1000, 1000, 1000)]);
    expect(placements).toEqual([]);
    expect(skipped.map((l) => l.id).sort()).toEqual(FLOOR_ZERO.map((l) => l.id).sort());
  });

  it("places what fits and reports the rest when sections stack past the bound", () => {
    const locations = Array.from({ length: 400 }, (_, i) => [
      loc(`rack-${i}`, "rack", `R${String(i).padStart(3, "0")}`),
      loc(`shelf-${i}`, "shelf", `R${String(i).padStart(3, "0")}-S1`, `rack-${i}`),
    ]).flat();
    const { placements, skipped } = arrangeUnplaced(locations, []);
    expect(skipped.length).toBeGreaterThan(0);
    expect(placements.length + skipped.length).toBe(locations.length);
    for (const p of placements) {
      expect(p.y + p.depth).toBeLessThanOrEqual(MAX_COORDINATE_MM);
      expect(p.x + p.width).toBeLessThanOrEqual(MAX_COORDINATE_MM);
    }
    expect(overlappingPlacements(boxesOf(placements))).toEqual([]);
  });
});

describe("findFreeSpot", () => {
  it("starts an empty floor at its margin", () => {
    expect(findFreeSpot([], { width: 1000, depth: 600 })).toEqual({ x: 500, y: 500 });
  });

  it("takes the open end of the row it can see", () => {
    expect(findFreeSpot([placement("a", 500, 500)], { width: 1000, depth: 600 })).toEqual({ x: 1800, y: 500 });
  });

  it("starts a new row beneath once the row is as wide as the floor goes", () => {
    const row = Array.from({ length: 7 }, (_, i) => placement(`t${i}`, 500 + i * 1300, 500));
    expect(findFreeSpot(row, { width: 1000, depth: 600 })).toEqual({ x: 500, y: 1400 });
  });

  it("goes beneath everything on a floor too full to search", () => {
    const column = Array.from({ length: 200 }, (_, i) => placement(`t${i}`, 500, 500 + i * 900, 1000, 600));
    // Bottom of the last tile is 500 + 199 * 900 + 600.
    expect(findFreeSpot(column, { width: 1000, depth: 600 })).toEqual({ x: 500, y: 500 + 199 * 900 + 600 + 1200 });
  });

  it("keeps the coordinate inside the server's bound even beneath a floor that reaches it", () => {
    const crowded = [
      ...Array.from({ length: 150 }, (_, i) => placement(`t${i}`, i * 1100, 0)),
      placement("low", 0, MAX_COORDINATE_MM - 600, 1000, 600),
    ];
    // Too many to search for a hole, and nowhere left beneath: the spot is clamped so the save is
    // accepted, and "Check for problems" is where the resulting overlap is reported.
    const spot = findFreeSpot(crowded, { width: 1000, depth: 600 });
    expect(spot.y + 600).toBeLessThanOrEqual(MAX_COORDINATE_MM);
  });

  it("never lands on anything, however many of mixed sizes go down one after another", () => {
    const sizes = [
      { width: 1000, depth: 600 },
      { width: 2400, depth: 1000 },
      { width: 3000, depth: 2000 },
      { width: 1800, depth: 600 },
      { width: 4000, depth: 800 },
    ];
    let placed: EditorPlacement[] = [];
    for (let i = 0; i < 60; i += 1) {
      const size = sizes[i % sizes.length];
      if (!size) throw new Error("no size");
      const { x, y } = findFreeSpot(placed, size);
      placed = [...placed, { locationId: `p${i}`, x, y, z: null, width: size.width, depth: size.depth, height: null }];
    }
    expect(overlappingPlacements(boxesOf(placed))).toEqual([]);
    expect(placed.every((p) => p.x >= 0 && p.y >= 0)).toBe(true);
  });

  it("finds room beside an auto-arranged layout without overlapping it", () => {
    const { placements } = arrangeUnplaced(FLOOR_ZERO, []);
    const size = footprintFor("rack", "Z2-R1");
    const { x, y } = findFreeSpot(placements, size);
    const next = [...placements, { locationId: "new", x, y, z: null, width: size.width, depth: size.depth, height: null }];
    expect(overlappingPlacements(boxesOf(next))).toEqual([]);
  });
});
