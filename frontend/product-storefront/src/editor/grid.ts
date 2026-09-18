/**
 * The canvas geometry: a 12-column grid, rows of a fixed height, and one collision rule.
 *
 * Every number a section's layout can take passes through `clampRect`, and every mutation that
 * can make two sections overlap passes through `resolveCollisions`. Both are pure so the rules
 * can be pinned by tests rather than discovered by dragging.
 *
 * **Collision: push-down, never overlap.** A section dropped, moved or grown onto another pushes
 * that one down far enough to clear, cascading. The alternative — allowing overlap and settling
 * it with a z-order — reads fine on desktop and falls apart at the breakpoint, where sections
 * stack by `y`: two sections sharing a row have no defined order once they are full width, so the
 * phone layout would depend on a stacking rule the merchant never sees. Push-down keeps the
 * desktop layout and the stacked layout telling the same story.
 *
 * Nothing compacts upward. A gap the merchant left stays where they left it; a layout that
 * re-flowed every time something above it was deleted would be impossible to aim.
 */

import type { SectionInstance } from "@/theme/schema";
import { GRID_COLUMNS, ROW_HEIGHT } from "@/theme/schema";

/** Columns across the canvas, from the schema — never a second copy of the number. */
export const COLUMNS = GRID_COLUMNS;

export const MIN_W = 1;
export const MIN_H = 1;

/** A ceiling, so a runaway resize cannot produce a canvas no one can scroll out of. */
export const MAX_H = 60;

/** A layout rectangle in grid units — the shape of `SectionInstance["layout"]`. */
export interface Rect {
  x: number;
  y: number;
  w: number;
  h: number;
}

function clampInt(value: number, min: number, max: number): number {
  // A document can arrive from anywhere; `NaN` resolves to the floor rather than poisoning every
  // comparison downstream.
  if (!Number.isFinite(value)) return min;
  return Math.min(max, Math.max(min, Math.round(value)));
}

/**
 * The only way a rectangle enters the document.
 *
 * Width first, then `x` against that width — so a section dragged past the right edge slides back
 * inside rather than being silently narrowed, and one resized past the edge stops growing.
 */
export function clampRect(rect: Rect): Rect {
  const w = clampInt(rect.w, MIN_W, COLUMNS);
  const h = clampInt(rect.h, MIN_H, MAX_H);
  return {
    w,
    h,
    x: clampInt(rect.x, 0, COLUMNS - w),
    y: clampInt(rect.y, 0, Number.MAX_SAFE_INTEGER),
  };
}

export function overlaps(a: Rect, b: Rect): boolean {
  return a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h;
}

/**
 * Push every section that collides with the anchor — the one the user just placed — far enough
 * down to clear it, then repeat for whatever that displaced.
 *
 * The anchor keeps exactly the rectangle it was given: the section under the pointer must never
 * be the one that moves, or dragging turns into a fight. Everything else is settled in reading
 * order (top row first, then left to right) so the same drag always produces the same result.
 */
export function resolveCollisions(
  sections: SectionInstance[],
  anchorId: string,
): SectionInstance[] {
  const anchor = sections.find((section) => section.id === anchorId);
  if (!anchor) return sections;

  const settled: SectionInstance[] = [anchor];
  const moved = new Map<string, SectionInstance>();

  const rest = sections
    .filter((section) => section.id !== anchorId)
    .slice()
    .sort((a, b) => a.layout.y - b.layout.y || a.layout.x - b.layout.x);

  for (const section of rest) {
    let layout = section.layout;
    // Bounded: each pass moves the section strictly below one already-settled section, and there
    // are finitely many of those.
    for (let guard = 0; guard <= settled.length; guard += 1) {
      const hit = settled.find((other) => overlaps(other.layout, layout));
      if (!hit) break;
      layout = { ...layout, y: hit.layout.y + hit.layout.h };
    }
    const next = layout === section.layout ? section : { ...section, layout };
    settled.push(next);
    if (next !== section) moved.set(section.id, next);
  }

  if (moved.size === 0) return sections;
  // Array order is z-order and tie-break stacking order; resolving collisions must not disturb it.
  return sections.map((section) => moved.get(section.id) ?? section);
}

/** The first free row under everything on the page — where a new section lands by default. */
export function bottomRow(sections: SectionInstance[]): number {
  return sections.reduce((max, section) => Math.max(max, section.layout.y + section.layout.h), 0);
}

/** The order sections stack in below the breakpoint: down the page, then across, then as placed. */
export function stackOrder(sections: SectionInstance[]): SectionInstance[] {
  return sections
    .map((section, index) => ({ section, index }))
    .sort(
      (a, b) =>
        a.section.layout.y - b.section.layout.y ||
        a.section.layout.x - b.section.layout.x ||
        a.index - b.index,
    )
    .map((entry) => entry.section);
}

/**
 * The pixel size of one grid step, matching what CSS Grid does with `repeat(12, 1fr)` and a gap:
 * `n` columns of `c` with `n - 1` gaps of `g` fill `width`, so each column starts one
 * `(width + g) / n` after the last.
 *
 * The canvas and the storefront lay out with the same rule, so a drag lands where the buyer will
 * see the section rather than where an approximation of the grid put it.
 */
export function gridSteps(width: number, gap: number): { column: number; row: number } {
  return { column: (width + gap) / COLUMNS, row: ROW_HEIGHT + gap };
}

/** The cell under a point, measured from the grid's top-left corner. */
export function cellFromPoint(
  offsetX: number,
  offsetY: number,
  width: number,
  gap: number,
): { x: number; y: number } {
  const steps = gridSteps(width, gap);
  if (steps.column <= 0 || steps.row <= 0) return { x: 0, y: 0 };
  return {
    x: Math.max(0, Math.min(COLUMNS - 1, Math.floor(offsetX / steps.column))),
    y: Math.max(0, Math.floor(offsetY / steps.row)),
  };
}
