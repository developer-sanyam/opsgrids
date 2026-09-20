/**
 * Turning a `product-source` setting into a list of lines.
 *
 * A source that matches nothing returns an empty array rather than throwing or silently falling
 * back to the whole catalogue: a merchant who points a grid at a category they have since emptied
 * should see the section's empty state, not a grid of unrelated stock.
 */

import type { Product } from "@/api/types";

export function resolveProducts(source: string, products: Product[], limit: number): Product[] {
  const matched = matchSource(source, products);
  return limit > 0 ? matched.slice(0, limit) : matched;
}

function matchSource(source: string, products: Product[]): Product[] {
  if (source === "featured") return products.filter((p) => p.featured);
  if (source === "in-stock") return products.filter((p) => p.in_stock);
  if (source.startsWith("category:")) {
    const key = source.slice("category:".length);
    return products.filter((p) => p.category === key);
  }
  // "all", and anything a document carries that this build does not recognise.
  return products;
}

/** What to tell the merchant when a source matched nothing. */
export function emptySourceHint(source: string): string {
  if (source === "featured") return "No line in this catalogue is marked as featured yet.";
  if (source === "in-stock") return "Every line in this catalogue is currently on lead time.";
  if (source.startsWith("category:")) return "This category has no lines in the catalogue yet.";
  return "This catalogue has no lines yet.";
}
