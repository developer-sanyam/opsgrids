import { describe, expect, it } from "vitest";

import type { LocationOut } from "@/api/types";
import { computeWarehouseLayout, layoutBounds } from "./warehouseLayout";

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

describe("computeWarehouseLayout", () => {
  it("is empty for no locations — the caller renders the empty state, not an empty scene", () => {
    expect(computeWarehouseLayout([])).toEqual([]);
  });

  it("places a single root at the ground level, sized to the default edge", () => {
    const zone = loc({ id: "zone", code: "ZONE-A", kind: "zone" });
    const [node] = computeWarehouseLayout([zone]);
    expect(node).toBeDefined();
    expect(node?.location.id).toBe("zone");
    expect(node?.position[1]).toBeCloseTo(0.5); // depth 0: y = 0 * rise + edge/2
    expect(node?.size).toEqual([1, 1, 1]);
  });

  it("raises each tree level above its parent and keeps a child horizontally near it", () => {
    const zone = loc({ id: "zone", code: "ZONE-A", kind: "zone" });
    const bin = loc({ id: "bin", code: "ZONE-A-B1", kind: "bin", parent_id: "zone" });
    const nodes = computeWarehouseLayout([zone, bin]);
    const zoneNode = nodes.find((n) => n.location.id === "zone");
    const binNode = nodes.find((n) => n.location.id === "bin");
    expect(zoneNode).toBeDefined();
    expect(binNode).toBeDefined();
    expect(binNode!.position[1]).toBeGreaterThan(zoneNode!.position[1]);
    // Nested under its only parent, so it sits at the parent's own (x, z) centre.
    expect(binNode!.position[0]).toBeCloseTo(zoneNode!.position[0]);
    expect(binNode!.position[2]).toBeCloseTo(zoneNode!.position[2]);
  });

  it("keeps every root cluster's own footprint apart — no two clusters share ground space", () => {
    const zoneA = loc({ id: "a", code: "ZONE-A", kind: "zone", pick_sequence: 1 });
    const zoneB = loc({ id: "b", code: "ZONE-B", kind: "zone", pick_sequence: 2 });
    const nodes = computeWarehouseLayout([zoneA, zoneB]);
    const a = nodes.find((n) => n.location.id === "a")!;
    const b = nodes.find((n) => n.location.id === "b")!;
    const dx = Math.abs(a.position[0] - b.position[0]);
    const dz = Math.abs(a.position[2] - b.position[2]);
    expect(Math.max(dx, dz)).toBeGreaterThan((a.size[0] + b.size[0]) / 2);
  });

  it("orders siblings by pick_sequence, nulls last, then code — same order as the 2D tree", () => {
    const zone = loc({ id: "zone", code: "ZONE-A", kind: "zone" });
    const b2 = loc({ id: "b2", code: "B2", kind: "bin", parent_id: "zone", pick_sequence: null });
    const b1 = loc({ id: "b1", code: "B1", kind: "bin", parent_id: "zone", pick_sequence: 2 });
    const b0 = loc({ id: "b0", code: "B0", kind: "bin", parent_id: "zone", pick_sequence: 1 });
    const nodes = computeWarehouseLayout([zone, b2, b1, b0]);
    // Everything but the zone itself is a child of "zone"; their placement order (and so their
    // grid cell) must follow flattenTree's own order: pick_sequence 1, then 2, then the null.
    const childOrder = nodes.filter((n) => n.location.parent_id === "zone").map((n) => n.location.code);
    expect(childOrder).toEqual(["B0", "B1", "B2"]);
  });

  it("scales a box by volume, relative to the largest volume in the warehouse, when set", () => {
    const small = loc({ id: "s", code: "S", max_volume_milli: 1000 });
    const large = loc({ id: "l", code: "L", max_volume_milli: 10000 });
    const nodes = computeWarehouseLayout([small, large]);
    const sSize = nodes.find((n) => n.location.id === "s")!.size[0];
    const lSize = nodes.find((n) => n.location.id === "l")!.size[0];
    expect(lSize).toBeGreaterThan(sSize);
  });

  it("falls back to weight when volume is not set", () => {
    const light = loc({ id: "s", code: "S", max_weight_grams: 500 });
    const heavy = loc({ id: "h", code: "H", max_weight_grams: 20000 });
    const nodes = computeWarehouseLayout([light, heavy]);
    const sSize = nodes.find((n) => n.location.id === "s")!.size[0];
    const hSize = nodes.find((n) => n.location.id === "h")!.size[0];
    expect(hSize).toBeGreaterThan(sSize);
  });

  it("gives a location with neither capacity field the plain default size — never a guess", () => {
    const bare = loc({ id: "bare", code: "BARE" });
    const withVolume = loc({ id: "v", code: "V", max_volume_milli: 5000 });
    const [bareNode] = computeWarehouseLayout([bare, withVolume]).filter((n) => n.location.id === "bare");
    expect(bareNode?.size).toEqual([1, 1, 1]);
  });

  it("never fabricates a node for a location outside the tree, and does not hang on a cycle", () => {
    const a = loc({ id: "a", code: "A", parent_id: "b" });
    const b = loc({ id: "b", code: "B", parent_id: "a" });
    let nodes: ReturnType<typeof computeWarehouseLayout> = [];
    expect(() => {
      nodes = computeWarehouseLayout([a, b]);
    }).not.toThrow();
    // Neither is a root (parent_id null); a well-formed tree reaches neither, same as flattenTree.
    expect(nodes).toEqual([]);
  });

  it("is deterministic — the same tree always lays out the same way", () => {
    const zone = loc({ id: "zone", code: "ZONE-A", kind: "zone" });
    const bin = loc({ id: "bin", code: "B1", kind: "bin", parent_id: "zone" });
    expect(computeWarehouseLayout([zone, bin])).toEqual(computeWarehouseLayout([zone, bin]));
  });
});

describe("layoutBounds", () => {
  it("returns a fixed, non-empty box for an empty layout", () => {
    expect(layoutBounds([])).toEqual({ minX: -1, maxX: 1, minZ: -1, maxZ: 1, maxY: 1 });
  });

  it("encloses every node's footprint, not just its centre", () => {
    const zone = loc({ id: "zone", code: "ZONE-A", kind: "zone" });
    const bin = loc({ id: "bin", code: "B1", kind: "bin", parent_id: "zone" });
    const nodes = computeWarehouseLayout([zone, bin]);
    const bounds = layoutBounds(nodes);
    for (const node of nodes) {
      const [x, , z] = node.position;
      const [w, , d] = node.size;
      expect(x - w / 2).toBeGreaterThanOrEqual(bounds.minX - 1e-9);
      expect(x + w / 2).toBeLessThanOrEqual(bounds.maxX + 1e-9);
      expect(z - d / 2).toBeGreaterThanOrEqual(bounds.minZ - 1e-9);
      expect(z + d / 2).toBeLessThanOrEqual(bounds.maxZ + 1e-9);
    }
    expect(bounds.maxY).toBeGreaterThan(0);
  });
});
