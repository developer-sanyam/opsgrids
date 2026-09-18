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
}

export interface Category {
  key: CategoryKey;
  label: string;
  blurb: string;
  /** Lines carried in this category — the count the tiles show. */
  line_count: number;
}

/** The merchant's live store document. `null` until a theme is adopted. */
export type StoreResponse = StoreDoc | null;
