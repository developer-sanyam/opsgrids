/**
 * The vocabulary a `product-source` setting may hold, and the fixture catalogue's own category
 * keys.
 *
 * **A merchant's real category is `storefront.categories.key` — a merchant-typed string
 * (`CategoryKeyField` in `services/storefront/app/schemas.py`, pattern `^[a-z0-9][a-z0-9-]*$`),
 * not a member of a compile-time union.** `Product.category` and `Category.key` in
 * `@/api/types` are therefore typed `string`: a merchant's catalogue is not bounded by what this
 * build's ten demo categories happen to name, and a source that matches nothing renders the
 * section's own empty state rather than failing to compile. `TRADE_CATEGORIES` /
 * `RETAIL_CATEGORIES` below stay a closed vocabulary for exactly one reason now — they are what
 * `api/fixtures.ts`'s sample catalogue is written against — and the editor's product-source
 * picker no longer reads them: `SettingControl` builds that list from the merchant's own fetched
 * categories (`@/catalogue/api`'s `listCategories`), with `BASE_PRODUCT_SOURCE_OPTIONS` below
 * supplying the three collections every catalogue has regardless of its categories.
 */

/** The trade vocabulary: what the six B2B themes sell. */
export const TRADE_CATEGORIES = [
  { key: "fasteners", label: "Fasteners & Fixings" },
  { key: "bearings", label: "Bearings & Power Transmission" },
  { key: "cables", label: "Cables & Wiring" },
  { key: "switchgear", label: "Switchgear & Protection" },
  { key: "cement", label: "Cement & Binders" },
  { key: "steel", label: "Structural Steel" },
  { key: "packaging", label: "Packaging & Consumables" },
  { key: "agri", label: "Agri Inputs" },
  { key: "tools", label: "Tools & Abrasives" },
  { key: "safety", label: "Safety & PPE" },
] as const;

/**
 * The retail vocabulary: what a `retail` theme sells — a quick-commerce grocery aisle and a
 * furniture showroom.
 *
 * Both live in one flat list because `product-source` is a flat string setting and a section saved
 * against `category:dairy` must keep meaning whatever theme is adopted later. The *catalogue* is
 * sliced by mode (see `api/fixtures.ts`); the vocabulary is not.
 */
export const RETAIL_CATEGORIES = [
  { key: "staples", label: "Atta, Rice & Dal" },
  { key: "dairy", label: "Dairy, Bread & Eggs" },
  { key: "snacks", label: "Snacks & Namkeen" },
  { key: "beverages", label: "Tea, Coffee & Drinks" },
  { key: "fresh", label: "Fruits & Vegetables" },
  { key: "household", label: "Cleaning & Household" },
  { key: "seating", label: "Sofas & Seating" },
  { key: "dining", label: "Dining & Tables" },
  { key: "beds", label: "Beds & Wardrobes" },
  { key: "lighting", label: "Lighting & Décor" },
] as const;

export const CATEGORIES = [...TRADE_CATEGORIES, ...RETAIL_CATEGORIES] as const;

/** Keys a `trade` fixture catalogue carries. */
export type TradeCategoryKey = (typeof TRADE_CATEGORIES)[number]["key"];

/** Keys a `retail` fixture catalogue carries. */
export type RetailCategoryKey = (typeof RETAIL_CATEGORIES)[number]["key"];

/**
 * The three collections every catalogue has, whatever categories a merchant has created. A
 * `product-source` control's real category options are appended at render time from the
 * merchant's own catalogue — see this module's header comment.
 */
export const BASE_PRODUCT_SOURCE_OPTIONS: { value: string; label: string }[] = [
  { value: "all", label: "Whole catalogue" },
  { value: "featured", label: "Featured items" },
  { value: "in-stock", label: "In stock now" },
];

/** The fixture catalogue's own label for one of its compile-time keys. */
export function categoryLabel(key: string): string {
  return CATEGORIES.find((c) => c.key === key)?.label ?? key;
}
