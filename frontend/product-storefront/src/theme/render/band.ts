/**
 * The vocabulary the three *band* sections share — `hero`, `image-with-text` and `rich-text`.
 *
 * All three answer the same three questions: what ground do the words sit on, what sits between a
 * photograph and the words on top of it, and where does the copy start when the section spans the
 * page instead of the content column. Answering them once here is what keeps the scrim from being
 * re-derived — slightly differently — in three files, which is exactly how the "no literal colour
 * in `render/`" rule erodes.
 *
 * Nothing in here is a literal colour or a literal length: a ground is a theme token or a value
 * the merchant typed into a `color` control, and every inset is `step()` or `pageGutter()`.
 */

import type { CSSProperties } from "react";

import { pageGutter } from "./pageGrid";
import { T } from "./tokens";

export const BAND_LAYOUTS = ["split", "overlay"] as const;
export type BandLayout = (typeof BAND_LAYOUTS)[number];

export const BAND_OVERLAYS = ["none", "scrim", "gradient"] as const;
export type BandOverlay = (typeof BAND_OVERLAYS)[number];

export const IMAGE_FOCUS = ["center", "top", "bottom", "left", "right"] as const;
export type ImageFocus = (typeof IMAGE_FOCUS)[number];

/** `none` paints no ground at all, which is what a block of prose in the page flow wants. */
export const BAND_TONES = ["none", "surface", "accent", "contrast"] as const;
export type BandTone = (typeof BAND_TONES)[number];

export interface BandGround {
  /** The ground the words sit on. `"transparent"` when the band paints none. */
  background: string;
  /** The only foreground guaranteed to be legible on that ground. */
  foreground: string;
  /** Whether the ground needs an edge to separate it from the page. Only `surface` does. */
  bordered: boolean;
}

/**
 * The ground and the foreground for a tone, with a merchant's tint taking precedence.
 *
 * A tint is a brand *surface* — a promo panel's marigold, a band's pastel — so the words on it
 * stay the theme's own text colour rather than its accent text, which is paired with the accent
 * and with nothing else. That is also the honest reading of a brand yellow: it is a ground, not a
 * link colour, and `T.text` on it is the pair that passes.
 */
export function bandGround(tone: BandTone, tint: string | null = null): BandGround {
  if (tint !== null) return { background: tint, foreground: T.text, bordered: false };
  if (tone === "accent") return { background: T.accent, foreground: T.accentText, bordered: false };
  if (tone === "contrast") return { background: T.text, foreground: T.bg, bordered: false };
  if (tone === "surface") return { background: T.surface, foreground: T.text, bordered: true };
  return { background: "transparent", foreground: T.text, bordered: false };
}

/** How much of the ground shows through an even scrim. Enough to read a heading through. */
const SCRIM_OPACITY = 0.58;

/**
 * The layer between an overlay band's photograph and its words, or `null` for `none`.
 *
 * It is the band's own ground at partial opacity rather than a fixed black wash: a dark theme's
 * scrim has to be dark and a light theme's `contrast` ground already is, so deriving it from the
 * tone is the only version that works in all eight palettes. `transparent` is a keyword, not a
 * colour, so the gradient stays inside the rule.
 */
export function overlayLayer(overlay: BandOverlay, ground: string): CSSProperties | null {
  if (overlay === "none") return null;
  if (overlay === "gradient") {
    return { backgroundImage: `linear-gradient(to top, ${ground} 4%, transparent 78%)` };
  }
  return { background: ground, opacity: SCRIM_OPACITY };
}

/**
 * The horizontal inset a band's *content* takes.
 *
 * A bleeding section spans the canvas rails, so its own padding is no longer what a buyer measures
 * from the edge — left alone, a full-width band's first word would start at the window edge while
 * every section above it started at the content column. `max()` against the half-overflow keeps
 * the copy on the same vertical line as the rest of the page and collapses back to the band's own
 * padding on a canvas narrower than the theme's maximum width.
 *
 * Deliberately not `calc(50vw - …)`: the canvas is a fixed-width box — 390px in the editor
 * preview, 1280px scaled in the gallery miniature — so anything sized against the viewport paints
 * over the editor's own furniture. `100%` here is the section, which is the grid's full width.
 */
export function bandInline(bleed: boolean, stacked: boolean, base: string): string {
  if (!bleed) return base;
  return `max(${base}, calc((100% - ${T.maxWidth}) / 2 + ${pageGutter(stacked)}))`;
}
