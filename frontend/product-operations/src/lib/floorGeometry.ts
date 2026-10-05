/**
 * What a floor's *published* placements become on screen: a read-only 2-D plan and a 3-D scene.
 *
 * Placements arrive from the server in integer millimetres, but they are data off the wire and one
 * bad row (a zero-width rack, a `NaN`, a coordinate in light years) must never take the plan down —
 * `normalizePlacements` splits the response into rectangles that can be drawn and the ones that
 * cannot, and the screen lists the latter under "not on floor plan" instead of dropping them
 * silently or drawing garbage.
 *
 * Floor-plan axes follow the layout editor: `x_mm` runs right and `y_mm` runs *down* the plan, both
 * measured to the rectangle's top-left corner. The 3-D scene is in metres with Y up, so plan `y`
 * becomes scene `z` and the optional elevation `z_mm` becomes scene `y`.
 */
import type { PublishedPlacementOut } from "@/api/types";
import type { WarehouseLayoutNode } from "@/lib/warehouseLayout";

/** A coordinate beyond 100 km is not a warehouse; treating it as invalid keeps the plan's bounds sane. */
const MAX_ABS_MM = 100_000_000;
/** A placement with no recorded height still has to be a box you can see and click. */
export const DEFAULT_HEIGHT_MM = 1000;
/** Plan margin around the drawn rectangles, so the outermost one is not on the edge. */
export const PLAN_MARGIN_MM = 1000;
/** The plan never zooms in tighter than this, however small the floor. */
const MIN_PLAN_SPAN_MM = 4000;
/** More ids than this in an address is a mistake, not a search result: a view marks this many and
 *  says so when a link carried more. */
export const MAX_HIGHLIGHT_IDS = 100;

export interface FloorRect {
  locationId: string;
  code: string;
  name: string;
  kind: PublishedPlacementOut["kind"];
  parentId: string | null;
  x: number;
  y: number;
  width: number;
  depth: number;
  z: number | null;
  height: number | null;
}

export interface NormalizedPlacements {
  /** Rectangles that can be drawn, largest footprint first so a rack is under the bins on it. */
  drawable: FloorRect[];
  /** The rows that cannot be drawn — shown by code under "not on floor plan". */
  skipped: PublishedPlacementOut[];
}

function finite(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value) && Math.abs(value) <= MAX_ABS_MM;
}

function drawable(placement: PublishedPlacementOut): boolean {
  if (!finite(placement.x_mm) || !finite(placement.y_mm)) return false;
  if (!finite(placement.width_mm) || placement.width_mm <= 0) return false;
  if (!finite(placement.depth_mm) || placement.depth_mm <= 0) return false;
  if (placement.z_mm !== null && !finite(placement.z_mm)) return false;
  if (placement.height_mm !== null && (!finite(placement.height_mm) || placement.height_mm <= 0)) return false;
  return true;
}

/** Splits a published layout's placements into the drawable and the skipped. Never throws. */
export function normalizePlacements(placements: readonly PublishedPlacementOut[]): NormalizedPlacements {
  const out: FloorRect[] = [];
  const skipped: PublishedPlacementOut[] = [];
  const seen = new Set<string>();
  for (const p of placements) {
    if (!drawable(p)) {
      skipped.push(p);
      continue;
    }
    // A location is drawn once. The server dedupes, but a repeat id would be two React keys.
    if (seen.has(p.location_id)) continue;
    seen.add(p.location_id);
    out.push({
      locationId: p.location_id,
      code: p.code,
      name: p.name,
      kind: p.kind,
      parentId: p.parent_id,
      x: p.x_mm,
      y: p.y_mm,
      width: p.width_mm,
      depth: p.depth_mm,
      z: p.z_mm,
      height: p.height_mm,
    });
  }
  out.sort((a, b) => b.width * b.depth - a.width * a.depth || a.code.localeCompare(b.code));
  return { drawable: out, skipped };
}

export interface PlanBox {
  minX: number;
  minY: number;
  width: number;
  height: number;
}

/** The plan's viewBox in millimetres: every drawable rectangle plus a margin, never degenerate. */
export function planBox(rects: readonly FloorRect[]): PlanBox {
  if (rects.length === 0) return { minX: 0, minY: 0, width: MIN_PLAN_SPAN_MM, height: MIN_PLAN_SPAN_MM };
  let minX = Infinity;
  let minY = Infinity;
  let maxX = -Infinity;
  let maxY = -Infinity;
  for (const r of rects) {
    minX = Math.min(minX, r.x);
    minY = Math.min(minY, r.y);
    maxX = Math.max(maxX, r.x + r.width);
    maxY = Math.max(maxY, r.y + r.depth);
  }
  const width = Math.max(maxX - minX + PLAN_MARGIN_MM * 2, MIN_PLAN_SPAN_MM);
  const height = Math.max(maxY - minY + PLAN_MARGIN_MM * 2, MIN_PLAN_SPAN_MM);
  return { minX: minX - PLAN_MARGIN_MM, minY: minY - PLAN_MARGIN_MM, width, height };
}

/** Millimetres to scene metres. */
function metres(mm: number): number {
  return mm / 1000;
}

/**
 * Drawable rectangles as the 3-D scene's nodes: centre position and box size in metres, plan `y` on
 * the scene's Z axis and elevation on its Y axis. A placement with no elevation sits on the ground
 * and one with no height is `DEFAULT_HEIGHT_MM` tall.
 */
export function placementsToSceneNodes(rects: readonly FloorRect[]): WarehouseLayoutNode[] {
  return rects.map((r) => {
    const height = r.height ?? DEFAULT_HEIGHT_MM;
    const base = r.z ?? 0;
    return {
      location: { id: r.locationId, parent_id: r.parentId, kind: r.kind, code: r.code, name: r.name },
      position: [metres(r.x + r.width / 2), metres(base + height / 2), metres(r.y + r.depth / 2)],
      size: [metres(r.width), metres(height), metres(r.depth)],
    };
  });
}

export interface ParsedHighlight {
  /** The ids to mark: trimmed, de-duplicated, and no more than `MAX_HIGHLIGHT_IDS`. */
  ids: string[];
  /** How many distinct ids the link carried, so a screen can say when it marks fewer than that. */
  total: number;
}

/** `?highlight=a,b,c` as ids: trimmed, de-duplicated, capped. Blank or absent is no highlight. */
export function parseHighlight(param: string | null): ParsedHighlight {
  if (param === null) return { ids: [], total: 0 };
  const all = new Set<string>();
  for (const part of param.split(",")) {
    const id = part.trim();
    if (id !== "") all.add(id);
  }
  return { ids: Array.from(all).slice(0, MAX_HIGHLIGHT_IDS), total: all.size };
}

/** The ids `parseHighlight` keeps, for a screen that has no use for how many it dropped. */
export function parseHighlightIds(param: string | null): string[] {
  return parseHighlight(param).ids;
}

export interface HighlightSummary {
  /** Codes of the highlighted locations that are on this floor's plan, alphabetically — the plan's own draw order is by size, which reads as random aloud. */
  found: string[];
  /** How many of the requested ids are not on the plan (unplaced, another floor, or skipped). */
  missing: number;
}

/** What a highlight actually landed on, for the announcement and the visible status line. */
export function summarizeHighlight(rects: readonly FloorRect[], ids: readonly string[]): HighlightSummary {
  const wanted = new Set(ids);
  const found = rects
    .filter((r) => wanted.has(r.locationId))
    .map((r) => r.code)
    .sort((a, b) => a.localeCompare(b));
  return { found, missing: wanted.size - found.length };
}
