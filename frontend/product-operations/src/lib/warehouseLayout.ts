/**
 * A pure, client-side layout for the warehouse 3D view (Fulfilment F9 MVP).
 *
 * F7 (real floor-plan geometry — floors, layout versions, walk graph) has not been built; a
 * `Location` carries no x/y/z of its own (`docs/fulfilment/plan.md` F1/F5). Everything here is
 * therefore **computed**, not authored: it recursively arranges the location tree the same way
 * `flattenTree` already orders it for the 2D tree screen — grouped by `parent_id`, each group
 * sorted by `pick_sequence` (nulls last) then `code` — into a grid on a ground plane. This file
 * has no opinion about where a picker actually walks; `Warehouse3D.tsx` labels its output as an
 * auto-arranged approximation, never as the warehouse's real geometry.
 *
 * Reuses `flattenTree` for the tree walk and sibling order rather than re-deriving either: a
 * warehouse's location tree has exactly one source of truth for "what nests under what, in what
 * order", and the 2D screen and this layout must never quietly disagree about it.
 */
import type { LocationOut } from "@/api/types";
import { flattenTree } from "./locationTree";

export interface WarehouseLayoutNode {
  location: LocationOut;
  /** World-space centre, metres-ish (unitless — there is no real scale to be true to). */
  position: readonly [number, number, number];
  /** Box dimensions (width, height, depth), centred on `position`. */
  size: readonly [number, number, number];
}

/** The default cube edge for a location with neither capacity field set — most locations today
 *  (`docs/fulfilment/plan.md`: "No production location uses anything but `bin`… nothing writes
 *  `kind`" at F1 launch, and neither capacity field has a default). Never fabricated *up* from
 *  this; a location with no capacity data renders at this one flat size, nothing more. */
const DEFAULT_EDGE = 1;
/** Capacity-scaled boxes are clamped to this range so one outlier location (a bulk dock holding
 *  a truckload) cannot shrink every other box in the same warehouse to a speck by comparison. */
const MIN_EDGE = 0.6;
const MAX_EDGE = 2.4;
/** Gap between sibling boxes inside one grid cell, and between grid cells. */
const GAP = 0.4;
/** How far each tree depth rises above its parent — nested locations float above the container
 *  they sit inside, an "exploded diagram" reading that stays legible without real shelf heights. */
const LEVEL_RISE = 1.4;

function clampEdge(value: number): number {
  return Math.min(MAX_EDGE, Math.max(MIN_EDGE, value));
}

/**
 * A location's box edge length: scaled from whichever real capacity field is set (volume
 * preferred — it is the more direct measure of physical size; weight is a mass limit, not a
 * dimension), relative to the largest value of that same field anywhere in this warehouse. A
 * location with neither field renders at `DEFAULT_EDGE` — never a guessed number.
 */
function edgeLengthOf(location: LocationOut, maxVolume: number, maxWeight: number): number {
  if (location.max_volume_milli !== null && maxVolume > 0) {
    return clampEdge(MIN_EDGE + (location.max_volume_milli / maxVolume) * (MAX_EDGE - MIN_EDGE));
  }
  if (location.max_weight_grams !== null && maxWeight > 0) {
    return clampEdge(MIN_EDGE + (location.max_weight_grams / maxWeight) * (MAX_EDGE - MIN_EDGE));
  }
  return DEFAULT_EDGE;
}

/**
 * Recursively arranges `locations` (a warehouse's whole tree, as `listLocations` returns it) into
 * world positions: every root location (`parent_id === null` — a zone, or one of the other
 * top-level functional kinds) becomes its own cluster, clusters laid out on a simple grid on the
 * ground plane; each location's own children are laid out the same way, inside its footprint, one
 * tree level higher. Order within a grid always follows `flattenTree`'s sibling order.
 *
 * Deterministic and side-effect-free — the same tree always produces the same layout, which is
 * what makes it something a click handler and a legend can agree about, and something a test can
 * assert on without a WebGL context.
 */
export function computeWarehouseLayout(locations: LocationOut[]): WarehouseLayoutNode[] {
  if (locations.length === 0) return [];

  // `flattenTree`'s own DFS order already carries the correct sorted order within every parent —
  // grouping its output by `parent_id` recovers each parent's children, sorted, without redoing
  // the sort or the cycle-guard `flattenTree` already does.
  const rows = flattenTree(locations);
  const childrenByParent = new Map<string | null, LocationOut[]>();
  for (const row of rows) {
    const list = childrenByParent.get(row.location.parent_id) ?? [];
    list.push(row.location);
    childrenByParent.set(row.location.parent_id, list);
  }

  const reachable = rows.map((r) => r.location);
  const maxVolume = Math.max(0, ...reachable.map((l) => l.max_volume_milli ?? 0));
  const maxWeight = Math.max(0, ...reachable.map((l) => l.max_weight_grams ?? 0));
  const sizeOf = (location: LocationOut): [number, number, number] => {
    const edge = edgeLengthOf(location, maxVolume, maxWeight);
    return [edge, edge, edge];
  };

  // The grid-cell edge length needed to hold `location`'s whole subtree, memoised: a leaf's is
  // its own box; a container's is the square grid its children need, so a parent's cell is always
  // big enough for everything under it and siblings never overlap.
  const footprintCache = new Map<string, number>();
  function footprintOf(location: LocationOut): number {
    const cached = footprintCache.get(location.id);
    if (cached !== undefined) return cached;
    const children = childrenByParent.get(location.id) ?? [];
    const own = Math.max(sizeOf(location)[0], sizeOf(location)[2]);
    let value = own;
    if (children.length > 0) {
      const cols = Math.ceil(Math.sqrt(children.length));
      const gridRows = Math.ceil(children.length / cols);
      const cell = GAP + Math.max(...children.map(footprintOf));
      value = Math.max(own, Math.max(cols, gridRows) * cell);
    }
    footprintCache.set(location.id, value);
    return value;
  }

  const out: WarehouseLayoutNode[] = [];
  function placeNode(location: LocationOut, x: number, z: number, depth: number): void {
    const size = sizeOf(location);
    out.push({ location, position: [x, depth * LEVEL_RISE + size[1] / 2, z], size });
    placeChildren(childrenByParent.get(location.id) ?? [], x, z, depth + 1);
  }
  function placeChildren(children: LocationOut[], centerX: number, centerZ: number, depth: number): void {
    if (children.length === 0) return;
    const cols = Math.ceil(Math.sqrt(children.length));
    const gridRows = Math.ceil(children.length / cols);
    const cell = GAP + Math.max(...children.map(footprintOf));
    const gridWidth = cols * cell;
    const gridDepth = gridRows * cell;
    children.forEach((child, i) => {
      const col = i % cols;
      const row = Math.floor(i / cols);
      const x = centerX - gridWidth / 2 + cell * (col + 0.5);
      const z = centerZ - gridDepth / 2 + cell * (row + 0.5);
      placeNode(child, x, z, depth);
    });
  }

  placeChildren(childrenByParent.get(null) ?? [], 0, 0, 0);
  return out;
}

export interface WarehouseLayoutBounds {
  minX: number;
  maxX: number;
  minZ: number;
  maxZ: number;
  maxY: number;
}

/** The ground-plane and height extent of a computed layout — what the 3D scene needs to size its
 *  ground grid and frame its starting camera, without hand-picking a distance per warehouse. */
export function layoutBounds(nodes: WarehouseLayoutNode[]): WarehouseLayoutBounds {
  if (nodes.length === 0) return { minX: -1, maxX: 1, minZ: -1, maxZ: 1, maxY: 1 };
  let minX = Infinity;
  let maxX = -Infinity;
  let minZ = Infinity;
  let maxZ = -Infinity;
  let maxY = 0;
  for (const node of nodes) {
    const [x, y, z] = node.position;
    const [w, h, d] = node.size;
    minX = Math.min(minX, x - w / 2);
    maxX = Math.max(maxX, x + w / 2);
    minZ = Math.min(minZ, z - d / 2);
    maxZ = Math.max(maxZ, z + d / 2);
    maxY = Math.max(maxY, y + h / 2);
  }
  return { minX, maxX, minZ, maxZ, maxY };
}
