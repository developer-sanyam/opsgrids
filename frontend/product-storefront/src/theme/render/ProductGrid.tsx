import { readCardOptions } from "./cardOptions";
import { SectionEmpty, StoreHeading } from "./primitives";
import { ProductCard } from "./ProductCard";
import { emptySourceHint, resolveProducts } from "./products";
import { bool, clampNum, optional, str } from "./settings";
import { step } from "./tokens";
import type { SectionRenderProps } from "./types";

/**
 * The workhorse commerce section: a grid of catalogue lines from one source.
 *
 * The phone column count is the merchant's, not the browser's. `auto-fill` with a minimum track
 * used to decide it, which at 390px always came out at one card — a full-width card per line is
 * how a spreadsheet looks, not how a shop looks. Two is the default and a merchant can say one.
 */
export function ProductGrid({ settings, products, stacked, mode, width }: SectionRenderProps) {
  const heading = optional(settings, "heading");
  const source = str(settings, "source", "all");
  const limit = clampNum(settings, "limit", 8, 1, 48);
  const columns = clampNum(settings, "columns", 4, 2, 4);
  const mobileColumns = clampNum(settings, "mobileColumns", 2, 1, 4);
  const cols = stacked ? mobileColumns : columns;
  /* Two cards abreast on a phone cannot wear a hero button's padding; the card's own action drops
     a size with them. */
  const card = readCardOptions(settings, stacked && cols > 1 ? { size: "sm" } : {});
  const items = resolveProducts(source, products, limit);

  return (
    <section style={{ display: "flex", flexDirection: "column", gap: step(1.1), height: "100%" }}>
      {heading ? <StoreHeading width={width}>{heading}</StoreHeading> : null}
      {items.length === 0 ? (
        <SectionEmpty title="Nothing to show here yet" hint={emptySourceHint(source)} />
      ) : (
        <div
          style={{
            display: "grid",
            gridTemplateColumns: `repeat(${cols}, minmax(0, 1fr))`,
            gap: stacked && cols > 1 ? step(0.6) : step(0.9),
          }}
        >
          {items.map((product) => (
            <ProductCard
              key={product.id}
              product={product}
              mode={mode}
              showCode={bool(settings, "showCode", true)}
              showPrice={bool(settings, "showPrice", true)}
              showStock={bool(settings, "showStock", true)}
              {...card}
            />
          ))}
        </div>
      )}
    </section>
  );
}
