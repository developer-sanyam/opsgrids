import { useMemo, useRef } from "react";

import type { Category, Product } from "@/api/types";
import { useElementWidth } from "@/lib/useElementWidth";
import type { Theme } from "@/theme/schema";
import { StoreRenderer } from "@/theme/render";
import { adoptTheme } from "@/theme/store";
import { suggestedBrand } from "@/theme/themes";

/** The width the miniature is composed at before it is scaled down. */
const DESIGN_WIDTH = 1280;

/**
 * A theme's real home page, rendered small.
 *
 * It is the same component tree the buyer-facing route renders, composed at `DESIGN_WIDTH` and
 * scaled — not a screenshot and not a hand-drawn approximation. A preview that is a separate
 * drawing goes stale the first time a section changes, and then the gallery is lying about what
 * adopting will produce.
 *
 * The miniature is genuinely inert, three ways, because it contains a whole store page — links, an
 * enquiry form's inputs, a carousel with its own `tabIndex` — none of which is a real control here:
 *
 * - `inert` takes the subtree out of the tab order and out of hit-testing. `aria-hidden` alone did
 *   not: it hides from the accessibility tree while leaving every input inside it tabbable, which
 *   is the `aria-hidden-focus` defect.
 * - `pointer-events: none` on the scaled canvas, belt to `inert`'s braces.
 * - `overflow: hidden` with an explicit height on the frame. This is the one that matters for
 *   layout: the canvas inside is composed at `DESIGN_WIDTH` and is several thousand pixels tall
 *   before it is scaled, and `transform` does not shrink the layout box. Without the clip it would
 *   cover the card's own controls, and hit-testing — not just painting — would go to the
 *   miniature. Do not remove it. `ThemePreview.test.tsx` pins all three.
 */
export function ThemePreview({
  theme,
  products,
  categories,
  height = 232,
}: {
  theme: Theme;
  products: Product[];
  categories: Category[];
  height?: number;
}) {
  const frameRef = useRef<HTMLDivElement>(null);
  const width = useElementWidth(frameRef, 360);
  const scale = width / DESIGN_WIDTH;

  // The preview needs a store-shaped document; forking here keeps the miniature and the adopted
  // store identical by construction. `showChrome={false}` below means the brand never actually
  // paints, but the prop is still real data rather than a stand-in.
  const preview = useMemo(() => adoptTheme(theme), [theme]);
  const brand = useMemo(() => suggestedBrand(theme.key), [theme]);

  return (
    <div
      ref={frameRef}
      inert
      aria-hidden
      data-testid="theme-preview-frame"
      className="relative overflow-hidden rounded-tile border border-line"
      style={{ height }}
    >
      <div
        data-testid="theme-preview-canvas"
        style={{
          width: DESIGN_WIDTH,
          transform: `scale(${scale})`,
          transformOrigin: "top left",
          pointerEvents: "none",
        }}
      >
        <StoreRenderer
          store={preview}
          brand={brand}
          route="/"
          products={products}
          categories={categories}
          width={DESIGN_WIDTH}
          showChrome={false}
        />
      </div>
    </div>
  );
}
