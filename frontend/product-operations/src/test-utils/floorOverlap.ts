/**
 * A test-side port of the server's floor-layout overlap rule, so a test can ask "would publish
 * accept this layout" of whatever the editor produced without a server in the loop.
 *
 * It mirrors `app.domain.overlapping_placements` / `_boxes_overlap` in
 * `services/operations/app/domain.py` and is deliberately *not* the editor's own collision
 * logic: a test of the editor that borrowed the editor's idea of "overlap" would agree with it by
 * construction. The rule there is strict — two boxes that merely share an edge do not overlap.
 * Keep the two in step if the server's rule ever changes.
 */
import type { LayoutGeometryIn, LayoutValidationOut, LocationPlacementIn } from "@/api/types";

type Box = Pick<LocationPlacementIn, "location_id" | "x_mm" | "y_mm" | "width_mm" | "depth_mm">;

/** `LocationPlacementIn.width_mm` / `depth_mm` default to 1000 on the server. */
const DEFAULT_FOOTPRINT_MM = 1000;

function boxesOverlap(a: Box, b: Box): boolean {
  const aw = a.width_mm ?? DEFAULT_FOOTPRINT_MM;
  const ad = a.depth_mm ?? DEFAULT_FOOTPRINT_MM;
  const bw = b.width_mm ?? DEFAULT_FOOTPRINT_MM;
  const bd = b.depth_mm ?? DEFAULT_FOOTPRINT_MM;
  return a.x_mm < b.x_mm + bw && b.x_mm < a.x_mm + aw && a.y_mm < b.y_mm + bd && b.y_mm < a.y_mm + ad;
}

/** Every pair of placements whose footprints overlap, each pair once, in the server's order
 *  (sorted by location id). */
export function overlappingPlacements(placements: readonly Box[]): [string, string][] {
  const items = [...placements].sort((a, b) => (a.location_id < b.location_id ? -1 : a.location_id > b.location_id ? 1 : 0));
  const pairs: [string, string][] = [];
  items.forEach((a, i) => {
    for (const b of items.slice(i + 1)) {
      if (boxesOverlap(a, b)) pairs.push([a.location_id, b.location_id]);
    }
  });
  return pairs;
}

/** The overlap half of `POST /layout-versions/{id}/validate`, over a saved geometry. */
export function validateOverlaps(geometry: Pick<LayoutGeometryIn, "placements">): LayoutValidationOut {
  const issues = overlappingPlacements(geometry.placements).map(([a, b]) => ({
    kind: "overlap",
    message: `Locations ${a} and ${b} overlap on the floor plan`,
    location_ids: [a, b],
    node_id: null,
  }));
  return { is_valid: issues.length === 0, issues };
}
