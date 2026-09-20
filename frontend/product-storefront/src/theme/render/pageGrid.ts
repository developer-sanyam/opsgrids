/**
 * The page canvas's geometry: two bleed rails, twelve content columns, two rails.
 *
 * This is the module that decides how wide the content column is and where its edges fall. It is
 * pure CSS strings and no JSX so the renderer, the store chrome and a test can all ask the same
 * question and get the same answer.
 *
 * **Why the cap lives on the tracks.** A `max-width` ancestor is a box nothing inside it can
 * escape, which is what made full-bleed impossible. Moving the cap onto the grid's middle columns
 * leaves the grid itself full width, so a section can span the rails instead. Negative margins
 * against `50vw` are not the alternative: this canvas is a fixed-width box — 390px in the editor
 * preview, 1280px scaled down in the gallery miniature, both nested inside the Console shell — so
 * a section sized against the *viewport* would paint straight over the editor's own furniture.
 *
 * **The arithmetic reproduces the old wrapper exactly**, which is what keeps the six trade themes
 * pixel-identical:
 *
 * - The grid's column gap sits between a rail and the first content column, so what a buyer
 *   measures from the canvas edge is `rail + gap`. The rail's minimum is therefore the gutter
 *   *minus* the gap.
 * - A column's cap is `(maxWidth − 2 gutters − 11 gaps) / 12`, so twelve of them plus their gaps
 *   come to exactly the width the old `max-width` wrapper gave with its padding taken off.
 * - Stacked, the column gap is zero — there is one content track, so there are no gutters to draw
 *   between columns — and the rails are fixed at the gutter so the single track takes the rest.
 *   The rails survive stacking on purpose: bleed at phone width means edge to edge.
 *
 * Verified in Chrome rather than inferred: at a 1500px canvas on a theme with `space: 14` and
 * `maxWidth: 1320`, the content track measures 1277.88px and the chrome column 1278px — the same
 * column to within a rounding of the twelve-way division.
 */

import type { CSSProperties } from "react";

import { GRID_COLUMNS } from "../schema";
import { T, step } from "./tokens";

/**
 * The page gutter, in spacing steps: the distance from the canvas edge to the content column.
 *
 * These are the numbers `StoreRenderer`'s wrapper used to apply as horizontal padding, moved here
 * because the grid is what draws them now. Changing one moves the header, the footer and every
 * non-bleeding section together, which is the point of there being one pair of numbers.
 */
const GUTTER_STEPS = { stacked: 0.75, wide: 1.5 } as const;

/** The gap between grid tracks, in spacing steps. Also the row gap, at every width. */
const GAP_STEPS = 1;

/** The gutter as a CSS length, for anything that has to line up with the content column. */
export function pageGutter(stacked: boolean): string {
  return step(stacked ? GUTTER_STEPS.stacked : GUTTER_STEPS.wide);
}

/** The gap between columns. Zero when stacked, where there is only one column. */
export function pageColumnGap(stacked: boolean): string | number {
  return stacked ? 0 : step(GAP_STEPS);
}

/** The gap between rows, at every width. */
export function pageRowGap(): string {
  return step(GAP_STEPS);
}

/** `grid-template-columns` for the page canvas: `[bleed-start] rail … rail [bleed-end]`. */
export function pageGridTemplateColumns(stacked: boolean): string {
  if (stacked) {
    const rail = step(GUTTER_STEPS.stacked);
    return `[bleed-start] ${rail} [content-start] minmax(0, 1fr) [content-end] ${rail} [bleed-end]`;
  }
  const rail = step(GUTTER_STEPS.wide - GAP_STEPS);
  const columnMax = `calc((${T.maxWidth} - ${step(
    2 * GUTTER_STEPS.wide + (GRID_COLUMNS - 1) * GAP_STEPS,
  )}) / ${GRID_COLUMNS})`;
  return (
    `[bleed-start] minmax(${rail}, 1fr) ` +
    `repeat(${GRID_COLUMNS}, [content-start] minmax(0, ${columnMax})) [content-end] ` +
    `minmax(${rail}, 1fr) [bleed-end]`
  );
}

/**
 * The centred column the page's *chrome* sits in — the store header, the store footer, and the
 * page-level empty states.
 *
 * Sections are placed by the grid and may leave this column; chrome may not. Keeping both in one
 * module is what guarantees a header's left edge and a section's left edge agree to the pixel.
 */
export function contentColumnStyle(stacked: boolean): CSSProperties {
  return {
    boxSizing: "border-box",
    width: "100%",
    maxWidth: T.maxWidth,
    marginInline: "auto",
    paddingInline: pageGutter(stacked),
  };
}
