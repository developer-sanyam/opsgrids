import { readCardOptions } from "./cardOptions";
import { ActionLink, SectionEmpty, StoreHeading } from "./primitives";
import { ProductCard } from "./ProductCard";
import { emptySourceHint, resolveProducts } from "./products";
import { bool, choice, clampNum, optional, str } from "./settings";
import { step } from "./tokens";
import type { SectionRenderProps } from "./types";

const CTA_PLACEMENTS = ["trailing", "below"] as const;

/** Bounds on the merchant's card width, in px. Validation of typed data, not a design value. */
const CARD_WIDTH = { fallback: 230, floor: 140, ceiling: 420 } as const;

/**
 * How much of the canvas one card may take on a phone.
 *
 * Two cards and a slice of a third. That slice is the whole point: a row that ends flush at the
 * edge looks like a grid which has run out, and nobody swipes it.
 *
 * The number is 0.42 and not "about a half" because the fraction is of the **canvas**, while the
 * cards sit in the narrower content track inside it. Measured in Chrome at 390: a card at 0.46
 * comes out 179px wide against a 366px track, which is two cards and their gaps to within a
 * pixel — the exact flush edge this is meant to avoid. 0.42 gives 164px and leaves a fifth of the
 * third card showing.
 *
 * Against the canvas rather than `vw` on purpose: the canvas is a fixed-width box — 390px in the
 * editor preview, a scaled 1280 in the gallery miniature — so a viewport-relative card would be
 * several times too wide everywhere except a real phone.
 */
const PHONE_CARD_FRACTION = 0.42;

/**
 * A horizontally scrolling row of lines.
 *
 * Scroll, not a slider: no timer moving content under a reader, no arrow buttons that hide half
 * the row from a keyboard. The track is a real scroll container with `tabIndex={0}` and a label,
 * so it is reachable and scrollable by keyboard, and it scroll-snaps for a mouse or a thumb.
 */
export function ProductCarousel({ settings, products, stacked, width, mode }: SectionRenderProps) {
  const heading = optional(settings, "heading");
  const source = str(settings, "source", "all");
  const limit = clampNum(settings, "limit", 10, 1, 48);
  const ctaLabel = optional(settings, "ctaLabel");
  const ctaPlacement = choice(settings, "ctaPlacement", CTA_PLACEMENTS, "trailing");
  const cardWidth = clampNum(
    settings,
    "cardWidth",
    CARD_WIDTH.fallback,
    CARD_WIDTH.floor,
    CARD_WIDTH.ceiling,
  );
  const card = readCardOptions(settings, stacked ? { size: "sm" } : {});
  const items = resolveProducts(source, products, limit);

  const peek = Math.max(CARD_WIDTH.floor, Math.round(width * PHONE_CARD_FRACTION));
  const trackColumn = stacked
    ? `minmax(min(${cardWidth}px, ${peek}px), 1fr)`
    : `minmax(${cardWidth}px, 1fr)`;
  const gap = stacked ? step(0.5) : step(0.9);

  const cta = ctaLabel ? (
    <ActionLink
      href={str(settings, "ctaHref", "/store/catalogue")}
      variant="outline"
      size={stacked ? "sm" : "md"}
      {...(heading ? { ariaLabel: `${ctaLabel}, ${heading}` } : {})}
    >
      {ctaLabel}
    </ActionLink>
  ) : null;

  return (
    <section style={{ display: "flex", flexDirection: "column", gap: step(1), height: "100%" }}>
      <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", gap: step(1) }}>
        {/* `width`, not the window. `lg` is a fluid step, and a `StoreHeading` given no width falls
            back to a `vw` ramp — the browser viewport, which nothing in this app renders at: the
            editor composes at 390 or 1280 inside a wide window and the gallery composes at 1280 and
            scales the result down. Left off, a rail heading in the phone preview was set to the
            size it would take on the desktop the preview is running on. This was the last one. */}
        {heading ? (
          <StoreHeading size={stacked ? "md" : "lg"} width={width}>
            {heading}
          </StoreHeading>
        ) : (
          <span />
        )}
        {ctaPlacement === "trailing" ? cta : null}
      </div>
      {items.length === 0 ? (
        <SectionEmpty title="Nothing to show here yet" hint={emptySourceHint(source)} />
      ) : (
        <ul
          tabIndex={0}
          aria-label={heading ? `${heading} — scrollable list` : "Scrollable product list"}
          style={{
            display: "grid",
            gridAutoFlow: "column",
            gridAutoColumns: trackColumn,
            gap,
            overflowX: "auto",
            scrollSnapType: "x proximity",
            /* A thumb that overshoots the end of a rail must not walk the browser back a page. */
            overscrollBehaviorX: "contain",
            scrollPaddingInlineStart: gap,
            listStyle: "none",
            margin: 0,
            padding: `0 0 ${step(0.5)}`,
          }}
        >
          {items.map((product) => (
            <li key={product.id} style={{ scrollSnapAlign: "start" }}>
              <ProductCard
                product={product}
                mode={mode}
                showPrice={bool(settings, "showPrice", true)}
                showStock
                {...card}
              />
            </li>
          ))}
        </ul>
      )}
      {ctaPlacement === "below" && cta ? (
        <div style={{ display: "flex", justifyContent: "center" }}>{cta}</div>
      ) : null}
    </section>
  );
}
