/**
 * The wire contracts this app reads.
 *
 * There is no storefront service on this branch — nothing on the gateway serves any of this yet —
 * so these types are the shape the catalogue and store endpoints are *specified* to return, and
 * `client.ts` serves them from fixtures. They are written as the real contract rather than as a
 * convenient local shape, so standing the service up is a flag flip and not a rewrite.
 */

import type { CategoryKey } from "@/theme/sources";
import type { StoreDoc } from "@/theme/schema";

export interface Page<T> {
  items: T[];
  total: number;
}

/**
 * One purchasable presentation of a line — a pack size, a length, a finish.
 *
 * **A presentation model, deliberately.** A variant carries a price and a stock flag so a card can
 * offer "500 g / 1 kg / 5 L" without a second request, and nothing more. When the order engine
 * lands a variant becomes a separately-orderable SKU with its own MOQ and its own stock ledger, and
 * at that point sibling `Product` records become the right model and this array the wrong one: an
 * inventory system has to address a SKU directly, and a position in an array is not an address.
 * The array is right for today's presentation layer, where sibling records would make
 * `resolveProducts`' `limit: 8` show three products in a rail of eight and would triple
 * `Category.line_count`.
 */
export interface ProductVariant {
  id: string;
  /** What the selector shows: "500 g", "1 kg", "5 L". */
  label: string;
  /** A band, exactly as on `Product`: equal min and max means a single published price. */
  price_min_minor: number;
  price_max_minor: number;
  /** Printed MRP for this pack, or `null` when the merchant publishes none. Never a discount. */
  mrp_minor: number | null;
  in_stock: boolean;
}

/** A catalogue line. Money is integer minor units with an ISO currency, platform-wide. */
export interface Product {
  id: string;
  /** The merchant's own part code — what a buyer quotes on a PO. */
  code: string;
  name: string;
  brand: string;
  category: CategoryKey;
  /** The one line of specification a buyer scans for. */
  spec: string;
  /** The selling unit: "per 100 pcs", "per metre", "per 50kg bag". */
  unit: string;
  /** A band: equal min and max means a single published price. */
  price_min_minor: number;
  price_max_minor: number;
  currency: "INR";
  /** Whether the published price includes GST. Labelling, not arithmetic. */
  gst_inclusive: boolean;
  /** Minimum order quantity, in `moq_unit`. */
  moq: number;
  moq_unit: string;
  in_stock: boolean;
  /** Working days to dispatch. 0 for a stocked line. */
  lead_time_days: number;
  featured: boolean;
  /** Bundled sample art under `/img/…`, or `null` to fall back to the generated `ImagePlate`. */
  image_url: string | null;
  /**
   * The printed maximum retail price. **An MRP, never a stored discount.**
   *
   * The percentage is derived from this and the selling price at the moment it is printed
   * (`discountPercent` in `lib/format.ts`). Storing the percentage as well gives you two numbers
   * that can disagree, and the way they disagree in practice is that someone raises the selling
   * price and leaves the flag alone — so the store advertises "30% off" a price that went *up*.
   * One stored number cannot contradict the other.
   *
   * `null` when the merchant publishes no MRP, which is the ordinary case for a trade line.
   */
  mrp_minor: number | null;
  /**
   * Door-to-door delivery time in minutes — the quick-commerce promise, "8 mins".
   *
   * **Not `lead_time_days`**, and not derivable from it. `lead_time_days` is working days to
   * *dispatch* and is `0` for a stocked line, so printing it as an ETA would lie in both
   * directions at once: a stocked line would promise instant delivery, and a line on a seven-day
   * lead time would claim delivery when all that was promised was handover to a carrier. The two
   * fields coexist and mean different things — a retail card reads this one, a trade card that one.
   *
   * `null` when the merchant makes no delivery promise for this line.
   */
  delivery_eta_minutes: number | null;
  /**
   * Pack sizes offered for this line. **Required — an empty array for a single-pack line.**
   *
   * Not optional. With `exactOptionalPropertyTypes` on, an optional array makes every consumer
   * write `product.variants?.length ?? 0`, and one of them will forget. The list is always present
   * and sometimes empty.
   *
   * **Invariant:** when `variants` is non-empty, this product's own `price_min_minor`,
   * `price_max_minor` and `mrp_minor` equal `variants[0]`'s. `variants[0]` is the default pack and
   * the product-level numbers are the canonical "from" price, which is what lets a rail or a grid
   * that offers no variant selector still print a true price rather than the cheapest pack's or an
   * average of them. `lib/format.test.ts` guards this across every fixture record.
   */
  variants: ProductVariant[];
}

export interface Category {
  key: CategoryKey;
  label: string;
  blurb: string;
  /** Lines carried in this category — the count the tiles show. */
  line_count: number;
  /** Bundled sample art under `/img/…`, or `null` to fall back to the generated `ImagePlate`. */
  image_url: string | null;
}

/** The merchant's live store document. `null` until a theme is adopted. */
export type StoreResponse = StoreDoc | null;
