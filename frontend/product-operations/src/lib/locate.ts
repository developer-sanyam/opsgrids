/**
 * Shaping `GET /inventory/locate` for the screen: results grouped warehouse then floor, and the
 * address each "Show on floor" / "Show in 3-D" link points at.
 *
 * The server does the finding; nothing here re-filters or re-sorts what it decided beyond putting
 * the groups in reading order. Coordinates play no part: a result carries floor-plan geometry in
 * millimetres at most, and this file only ever uses its presence.
 */
import type { LocateResultOut, PlacementReason } from "@/api/trackingTypes";

export interface FloorGroup {
  /** `null` for bins the server could not place on any floor. */
  floorId: string | null;
  floorName: string | null;
  results: LocateResultOut[];
}

export interface WarehouseGroup {
  warehouseId: string;
  warehouseName: string;
  floors: FloorGroup[];
  /** Distinct bins. The server answers one row per bin and status, so this is not `results.length`. */
  resultCount: number;
}

/** How many distinct bins `results` covers: a bin holding stock in two statuses is one bin. */
export function countBins(results: readonly LocateResultOut[]): number {
  return new Set(results.map((r) => r.location_id)).size;
}

/** Warehouses by name, floors by name with the floorless last, bins by code. */
export function groupLocateResults(results: readonly LocateResultOut[]): WarehouseGroup[] {
  const warehouses = new Map<string, WarehouseGroup>();
  for (const result of results) {
    let group = warehouses.get(result.warehouse_id);
    if (!group) {
      group = { warehouseId: result.warehouse_id, warehouseName: result.warehouse_name, floors: [], resultCount: 0 };
      warehouses.set(result.warehouse_id, group);
    }
    let floor = group.floors.find((f) => f.floorId === result.floor_id);
    if (!floor) {
      floor = { floorId: result.floor_id, floorName: result.floor_name, results: [] };
      group.floors.push(floor);
    }
    floor.results.push(result);
  }
  const byCode = (a: LocateResultOut, b: LocateResultOut) => a.code.localeCompare(b.code);
  const groups = Array.from(warehouses.values()).sort((a, b) => a.warehouseName.localeCompare(b.warehouseName));
  for (const group of groups) {
    group.floors.sort((a, b) => {
      if (a.floorName === null && b.floorName === null) return 0;
      if (a.floorName === null) return 1;
      if (b.floorName === null) return -1;
      return a.floorName.localeCompare(b.floorName);
    });
    for (const floor of group.floors) floor.results.sort(byCode);
    group.resultCount = countBins(group.floors.flatMap((f) => f.results));
  }
  return groups;
}

/**
 * The location id to highlight for a result: the bin itself when it is drawn, or the rack or zone
 * above it when the geometry is the ancestor's. `null` when there is nothing on a plan to mark.
 */
export function highlightIdFor(result: LocateResultOut): string | null {
  const placement = result.placement;
  if (placement === null) return null;
  if (placement.source === "ancestor") return placement.ancestor_location_id;
  return result.location_id;
}

/** The distinct location ids to highlight for `results`: a bin in two statuses, or two bins in one
 *  rack, are each marked once. */
export function highlightIdsFor(results: readonly LocateResultOut[]): string[] {
  return Array.from(new Set(results.map(highlightIdFor).filter((id): id is string => id !== null)));
}

/** The floor view for `results` on one floor, with every placed result highlighted. `null` when
 *  the floor is unknown or nothing there has a placement. */
export function floorViewHref(warehouseId: string, floorId: string | null, results: readonly LocateResultOut[]): string | null {
  if (floorId === null) return null;
  const ids = highlightIdsFor(results);
  if (ids.length === 0) return null;
  return `/warehouses/${encodeURIComponent(warehouseId)}/floors/${encodeURIComponent(floorId)}/view?highlight=${ids.map(encodeURIComponent).join(",")}`;
}

/** The auto-arranged 3-D view with this bin marked — for a bin that is on no published plan.
 *  `why=unplaced` carries the one reason that page cannot work out for itself: the warehouse *has*
 *  a published layout and this bin is not on it, so the view must not say there is no floor plan. */
export function autoArrangedHref(result: LocateResultOut): string {
  const why = result.placement_reason === "unplaced" ? "&why=unplaced" : "";
  return `/warehouses/${encodeURIComponent(result.warehouse_id)}/3d?highlight=${encodeURIComponent(result.location_id)}${why}`;
}

const REASON_LABEL: Record<PlacementReason, string> = {
  unplaced: "Not on floor plan",
  no_published_layout: "Floor has no published layout",
  no_permission: "Floor plan hidden — you can't read layouts",
};

/** The words for why a bin that holds stock has no place on a plan. A bin with neither a
 *  placement nor a reason is called unplaced rather than left unexplained. */
export function placementNote(result: LocateResultOut): string | null {
  if (result.placement !== null) {
    return result.placement.source === "ancestor" ? "Shown at the rack or zone it is in" : null;
  }
  return REASON_LABEL[result.placement_reason ?? "unplaced"];
}
