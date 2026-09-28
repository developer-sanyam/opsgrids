/**
 * One colour per `Location.kind`, for the warehouse 3D view (Fulfilment F9 MVP) — the legend, the
 * detail drawer's swatch, and the box mesh itself all read from here, so a kind is always the
 * same colour everywhere it appears in that view (the same reasoning `opsgrids-ui`'s status
 * registry documents for status words).
 *
 * `console.css`'s closed palette has exactly seven distinct hues outside the accent/neutral inks
 * (`accent`, `positive`/`present`, `info`, `cyan`, `warning`/`late`, `danger`, `neutral`) — adding
 * an eighth is what `opsgrids/tokens` exists to refuse. Eleven kinds cannot each get a hue no
 * other kind uses without one. They split cleanly into two groups that are never siblings in a
 * real tree, though: the seven kinds that sit at a warehouse's top level (`zone` plus the six
 * functional areas — ADR-0024 §5) and the four that usually nest under a zone (`aisle`, `rack`,
 * `shelf`, `bin`). Each group spends the same seven hues once. A box's label, the click-through
 * detail panel and the legend below all still say which kind it is in words — colour here is a
 * fast visual grouping, never the only signal (frontend/CLAUDE.md: state is never colour alone).
 */
import type { LocationKind } from "@/api/types";

interface KindColor {
  /** A whole Tailwind utility, written out so the token scanner finds it (`opsgrids-ui`'s
   *  `SERIES_TONE` documents why this cannot be assembled from a variable). Used for the legend
   *  and the detail drawer's swatch — plain DOM, not the 3D scene. */
  swatchClass: string;
  /** The same token's CSS custom property, for the one place a class name cannot reach: a
   *  three.js material's `color`, which needs an actual colour value at render time, not a
   *  class. `resolveCssColor` below reads it from the live stylesheet, so the box always matches
   *  the current theme (light or dark) without this file knowing which one is active. */
  cssVar: string;
}

export const LOCATION_KIND_COLOR: Record<LocationKind, KindColor> = {
  // Top-level kinds (zone, plus the six functional areas) — one hue each.
  zone: { swatchClass: "bg-accent", cssVar: "--color-accent" },
  staging: { swatchClass: "bg-late", cssVar: "--color-late" },
  pack_station: { swatchClass: "bg-cyan", cssVar: "--color-cyan" },
  dock: { swatchClass: "bg-info", cssVar: "--color-info" },
  receiving: { swatchClass: "bg-present", cssVar: "--color-present" },
  returns: { swatchClass: "bg-neutral", cssVar: "--color-neutral" },
  quarantine: { swatchClass: "bg-danger", cssVar: "--color-danger" },
  // Kinds that usually nest under a zone — the same seven hues, reused: these are compared
  // against their siblings inside one zone cluster, not against a dock across the warehouse.
  aisle: { swatchClass: "bg-cyan", cssVar: "--color-cyan" },
  rack: { swatchClass: "bg-info", cssVar: "--color-info" },
  shelf: { swatchClass: "bg-present", cssVar: "--color-present" },
  bin: { swatchClass: "bg-neutral", cssVar: "--color-neutral" },
};

/**
 * Resolves a token's current colour from the live stylesheet — light or dark, whichever the page
 * has active — for a three.js material. Falls back to the `disabled` ink token (itself read the
 * same way, never a literal colour) if the requested variable somehow isn't defined.
 */
export function resolveCssColor(cssVar: string, root: Element = document.documentElement): string {
  const styles = getComputedStyle(root);
  const value = styles.getPropertyValue(cssVar).trim();
  if (value) return value;
  return styles.getPropertyValue("--color-disabled").trim();
}
