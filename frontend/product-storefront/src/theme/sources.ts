/**
 * The vocabulary a `product-source` setting may hold.
 *
 * It lives in the theme layer, not the catalogue, on purpose: the editor renders these options
 * from a `SectionDef` with no catalogue loaded, and a section saved against a category must keep
 * meaning when the catalogue behind it changes. The catalogue conforms to these keys; a source
 * that matches nothing renders the section's own empty state rather than disappearing.
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

export type CategoryKey = (typeof CATEGORIES)[number]["key"];

/** Keys a `trade` catalogue carries. */
export type TradeCategoryKey = (typeof TRADE_CATEGORIES)[number]["key"];

/** Keys a `retail` catalogue carries. */
export type RetailCategoryKey = (typeof RETAIL_CATEGORIES)[number]["key"];

/** Every option a `product-source` control offers, in the order the palette shows them. */
export const PRODUCT_SOURCE_OPTIONS: { value: string; label: string }[] = [
  { value: "all", label: "Whole catalogue" },
  { value: "featured", label: "Featured items" },
  { value: "in-stock", label: "In stock now" },
  ...CATEGORIES.map((c) => ({ value: `category:${c.key}`, label: c.label })),
];

export function categoryLabel(key: string): string {
  return CATEGORIES.find((c) => c.key === key)?.label ?? key;
}
