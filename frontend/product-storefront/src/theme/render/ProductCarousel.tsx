import { ActionLink, SectionEmpty, StoreHeading } from "./primitives";
import { ProductCard } from "./ProductCard";
import { emptySourceHint, resolveProducts } from "./products";
import { bool, clampNum, optional, str } from "./settings";
import { step } from "./tokens";
import type { SectionRenderProps } from "./types";

/**
 * A horizontally scrolling row of lines.
 *
 * Scroll, not a slider: no timer moving content under a reader, no arrow buttons that hide half
 * the row from a keyboard. The track is a real scroll container with `tabIndex={0}` and a label,
 * so it is reachable and scrollable by keyboard, and it scroll-snaps for a mouse or a thumb.
 */
export function ProductCarousel({ settings, products }: SectionRenderProps) {
  const heading = optional(settings, "heading");
  const source = str(settings, "source", "all");
  const limit = clampNum(settings, "limit", 10, 1, 48);
  const ctaLabel = optional(settings, "ctaLabel");
  const items = resolveProducts(source, products, limit);

  return (
    <section style={{ display: "flex", flexDirection: "column", gap: step(1), height: "100%" }}>
      <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", gap: step(1) }}>
        {heading ? <StoreHeading>{heading}</StoreHeading> : <span />}
        {ctaLabel ? (
          <ActionLink href={str(settings, "ctaHref", "/store/catalogue")} variant="outline">
            {ctaLabel}
          </ActionLink>
        ) : null}
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
            gridAutoColumns: "minmax(230px, 1fr)",
            gap: step(0.9),
            overflowX: "auto",
            scrollSnapType: "x proximity",
            listStyle: "none",
            margin: 0,
            padding: `0 0 ${step(0.5)}`,
          }}
        >
          {items.map((product) => (
            <li key={product.id} style={{ scrollSnapAlign: "start" }}>
              <ProductCard product={product} showPrice={bool(settings, "showPrice", true)} showStock />
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
