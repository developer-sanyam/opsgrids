/**
 * Pure state helpers for the floor layout editor (Fulfilment plan §2/F7) — converting a loaded
 * `LayoutGeometryOut` into the editor's own working shape, converting edits back into the
 * `LayoutGeometryIn` the server's full-replace `PUT .../geometry` expects, and the small bits of
 * arithmetic (default placement position, node code generation) the canvas itself needs. No React
 * here, so the state transitions are unit-testable without a DOM.
 *
 * Overlap and disconnected-graph *validation* is deliberately **not** reimplemented here — the
 * server's `POST /layout-versions/{id}/validate` (`app.domain.overlapping_placements`/
 * `disconnected_required_nodes`) is the one place that logic lives, so the editor's "can I
 * publish" answer can never drift from the one the publish endpoint itself enforces.
 */
import type {
  LayoutGeometryIn,
  LayoutGeometryOut,
  LocationOut,
  WalkEdgeIn,
  WalkNodeIn,
} from "@/api/types";

export interface EditorPlacement {
  locationId: string;
  x: number;
  y: number;
  z: number | null;
  width: number;
  depth: number;
  height: number | null;
}

export interface EditorNode {
  code: string;
  locationId: string | null;
  x: number;
  y: number;
  z: number | null;
}

export interface EditorEdge {
  fromCode: string;
  toCode: string;
  distanceMm: number;
  isTraversable: boolean;
  bidirectional: boolean;
}

export interface EditorState {
  placements: EditorPlacement[];
  nodes: EditorNode[];
  edges: EditorEdge[];
}

export const EMPTY_STATE: EditorState = { placements: [], nodes: [], edges: [] };

/** The server's own default footprint (`LocationPlacementIn.width_mm`/`depth_mm` default 1000). */
export const DEFAULT_FOOTPRINT_MM = 1000;
/** Grid spacing for a newly-added location, and the canvas's default zoom-to-fit margin. */
const GRID_CELL_MM = 1500;
const GRID_COLUMNS = 6;

export function geometryToEditorState(geometry: LayoutGeometryOut): EditorState {
  return {
    placements: geometry.placements.map((p) => ({
      locationId: p.location_id,
      x: p.x_mm,
      y: p.y_mm,
      z: p.z_mm,
      width: p.width_mm,
      depth: p.depth_mm,
      height: p.height_mm,
    })),
    nodes: geometry.walk_nodes.map((n) => ({
      code: n.code,
      locationId: n.location_id,
      x: n.x_mm,
      y: n.y_mm,
      z: n.z_mm,
    })),
    edges: geometry.walk_edges.map((e) => ({
      fromCode: e.from_code,
      toCode: e.to_code,
      distanceMm: e.distance_mm,
      isTraversable: e.is_traversable,
      bidirectional: e.bidirectional,
    })),
  };
}

export function editorStateToGeometryIn(state: EditorState): LayoutGeometryIn {
  const walk_nodes: WalkNodeIn[] = state.nodes.map((n) => ({
    code: n.code,
    location_id: n.locationId,
    x_mm: Math.round(n.x),
    y_mm: Math.round(n.y),
    z_mm: n.z,
  }));
  const walk_edges: WalkEdgeIn[] = state.edges.map((e) => ({
    from_code: e.fromCode,
    to_code: e.toCode,
    distance_mm: Math.round(e.distanceMm),
    is_traversable: e.isTraversable,
    bidirectional: e.bidirectional,
  }));
  return {
    placements: state.placements.map((p) => ({
      location_id: p.locationId,
      x_mm: Math.round(p.x),
      y_mm: Math.round(p.y),
      z_mm: p.z,
      width_mm: Math.round(p.width),
      depth_mm: Math.round(p.depth),
      height_mm: p.height,
    })),
    walk_nodes,
    walk_edges,
  };
}

/** Deep-equal by value (via the same JSON shape both states round-trip through) — good enough
 *  for "has this draft changed since it last loaded or saved" without a diffing library, since
 *  editor state is always small (a floor's worth of locations and walk-graph nodes). */
export function statesEqual(a: EditorState, b: EditorState): boolean {
  const normalize = (s: EditorState) => ({
    placements: [...s.placements].sort((x, y) => x.locationId.localeCompare(y.locationId)),
    nodes: [...s.nodes].sort((x, y) => x.code.localeCompare(y.code)),
    edges: [...s.edges]
      .map((e) => ({ ...e }))
      .sort((x, y) => `${x.fromCode}\u0000${x.toCode}`.localeCompare(`${y.fromCode}\u0000${y.toCode}`)),
  });
  return JSON.stringify(normalize(a)) === JSON.stringify(normalize(b));
}

/** Where a newly-added location lands: simple left-to-right, top-to-bottom grid packing so it
 *  never stacks exactly on top of an existing placement. The user drags it from there. */
export function nextGridPosition(existingCount: number): { x: number; y: number } {
  const row = Math.floor(existingCount / GRID_COLUMNS);
  const col = existingCount % GRID_COLUMNS;
  return { x: col * GRID_CELL_MM, y: row * GRID_CELL_MM };
}

/** The walk node "for" a location — stable across saves (derived from the location's own id, not
 *  a counter), so re-saving the same draft twice never renames a node a previous save created. */
export function locationNodeCode(locationId: string): string {
  return `L-${locationId.replace(/[^A-Za-z0-9]/g, "").slice(0, 8).toUpperCase()}`;
}

/** The next free junction code (`J-1`, `J-2`, …) given the codes already in use. */
export function nextJunctionCode(existingCodes: ReadonlySet<string>): string {
  let n = 1;
  while (existingCodes.has(`J-${n}`)) n += 1;
  return `J-${n}`;
}

/** Every location in the warehouse not yet placed on this draft — the "add to canvas" sidebar. */
export function unplacedLocations(all: LocationOut[], state: EditorState): LocationOut[] {
  const placed = new Set(state.placements.map((p) => p.locationId));
  return all.filter((l) => !placed.has(l.id));
}

/** Straight-line distance between two points, rounded to a whole millimetre — the default a new
 *  edge's distance field starts from (the user can always type a longer real walking distance;
 *  this just saves typing the common case). */
export function euclideanDistanceMm(a: { x: number; y: number }, b: { x: number; y: number }): number {
  return Math.round(Math.hypot(a.x - b.x, a.y - b.y));
}
