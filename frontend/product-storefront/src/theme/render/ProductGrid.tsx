import { SectionEmpty, StoreHeading } from "./primitives";
import { ProductCard } from "./ProductCard";
import { emptySourceHint, resolveProducts } from "./products";
import { bool, choice, clampNum, optional, str } from "./settings";
import { step } from "./tokens";
import type { SectionRenderProps } from "./types";

const PRICE_NOTES = ["exclusive", "inclusive", "on-request"] as const;

/** The workhorse commerce section: a grid of catalogue lines from one source. */
export function ProductGrid({ settings, products, stacked }: SectionRenderProps) {
  const heading = optional(settings, "heading");
  const source = str(settings, "source", "all");
  const limit = clampNum(settings, "limit", 8, 1, 48);
  const columns = clampNum(settings, "columns", 4, 2, 4);
  const items = resolveProducts(source, products, limit);

  return (
    <section style={{ display: "flex", flexDirection: "column", gap: step(1.1), height: "100%" }}>
      {heading ? <StoreHeading>{heading}</StoreHeading> : null}
      {items.length === 0 ? (
        <SectionEmpty title="Nothing to show here yet" hint={emptySourceHint(source)} />
      ) : (
        <div
          style={{
            display: "grid",
            gridTemplateColumns: stacked
              ? "repeat(auto-fill, minmax(min(100%, 220px), 1fr))"
              : `repeat(${columns}, minmax(0, 1fr))`,
            gap: step(0.9),
          }}
        >
          {items.map((product) => (
            <ProductCard
              key={product.id}
              product={product}
              showCode={bool(settings, "showCode", true)}
              showPrice={bool(settings, "showPrice", true)}
              showStock={bool(settings, "showStock", true)}
              priceNote={choice(settings, "priceNote", PRICE_NOTES, "exclusive")}
            />
          ))}
        </div>
      )}
    </section>
  );
}
