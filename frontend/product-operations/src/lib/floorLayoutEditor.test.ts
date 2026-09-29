import { describe, expect, it } from "vitest";

import type { LayoutGeometryOut } from "@/api/types";
import {
  DEFAULT_FOOTPRINT_MM,
  editorStateToGeometryIn,
  euclideanDistanceMm,
  geometryToEditorState,
  locationNodeCode,
  nextGridPosition,
  nextJunctionCode,
  statesEqual,
  unplacedLocations,
} from "./floorLayoutEditor";
import type { EditorState } from "./floorLayoutEditor";

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
