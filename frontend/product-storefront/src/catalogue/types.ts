/**
 * The merchant catalogue's wire contract — `CategoryOut`/`CategoryCreate`/`CategoryPatch` and
 * `ProductOut`/`ProductCreate`/`ProductPatch`/`ProductVariantIn`/`ProductVariantOut`, transcribed
 * from `services/storefront/app/schemas.py`, not guessed.
 */

export interface PagedResult<T> {
  items: T[];
  total: number;
  page: number;
  pages: number;
  size: number;
}

export interface CategoryOut {
  id: string;
  key: string;
  label: string;
  blurb: string | null;
  image_url: string | null;
  sort_order: number;
  is_active: boolean;
  /** Live products carrying this category — grouped, never a per-row query. */
  line_count: number;
  created_at: string;
  updated_at: string;
}

export interface CategoryInput {
  key: string;
  label: string;
  blurb: string | null;
  image_url: string | null;
  sort_order: number;
  is_active: boolean;
}

/** `CategoryPatch` — every field optional; absent leaves it alone, `null` clears a nullable one.
 *  `key` is immutable once created (`services/storefront/app/schemas.py` `CategoryPatch`). */
export type CategoryPatch = Partial<Omit<CategoryInput, "key">>;

export interface ProductVariantInput {
  id?: string;
  label: string;
  price_min_minor: number;
  price_max_minor: number;
  mrp_minor: number | null;
  in_stock: boolean;
}

export interface ProductVariantOut {
  id: string;
  label: string;
  price_min_minor: number;
  price_max_minor: number;
  mrp_minor: number | null;
  in_stock: boolean;
}

export type ProductStatus = "draft" | "active" | "archived";

export interface ProductOut {
  id: string;
  code: string;
  name: string;
  brand: string;
  category_id: string;
  /** The category's `key`, resolved server-side. */
  category: string;
  spec: string;
  unit: string;
  description: string | null;
  price_min_minor: number;
  price_max_minor: number;
  mrp_minor: number | null;
  currency: string;
  gst_inclusive: boolean;
  moq: number;
  moq_unit: string;
  in_stock: boolean;
  lead_time_days: number;
  delivery_eta_minutes: number | null;
  featured: boolean;
  image_url: string | null;
  status: ProductStatus;
  is_active: boolean;
  variants: ProductVariantOut[];
  created_at: string;
  updated_at: string;
}

export interface ProductInput {
  code: string;
  name: string;
  brand: string;
  category_id: string;
  spec: string;
  unit: string;
  description: string | null;
  price_min_minor: number;
  price_max_minor: number;
  mrp_minor: number | null;
  gst_inclusive: boolean;
  moq: number;
  moq_unit: string;
  in_stock: boolean;
  lead_time_days: number;
  delivery_eta_minutes: number | null;
  featured: boolean;
  image_url: string | null;
  status: ProductStatus;
  is_active: boolean;
  variants: ProductVariantInput[];
}

/** `ProductPatch` — every field optional; `code` is immutable once created. */
export type ProductPatchInput = Partial<Omit<ProductInput, "code">>;

export type ProductSort = "name" | "-name" | "code" | "-code" | "price_min_minor" | "-price_min_minor" | "updated_at" | "-updated_at";

export interface ListProductsParams {
  q?: string;
  category_id?: string;
  status?: ProductStatus;
  in_stock?: boolean;
  featured?: boolean;
  sort?: ProductSort;
  page?: number;
  size?: number;
}

export interface ListCategoriesParams {
  q?: string;
  is_active?: boolean;
  page?: number;
  size?: number;
}
