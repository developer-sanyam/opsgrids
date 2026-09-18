/**
 * The vocabulary a `product-source` setting may hold.
 *
 * It lives in the theme layer, not the catalogue, on purpose: the editor renders these options
 * from a `SectionDef` with no catalogue loaded, and a section saved against a category must keep
 * meaning when the catalogue behind it changes. The catalogue conforms to these keys; a source
 * that matches nothing renders the section's own empty state rather than disappearing.
 */

export const CATEGORIES = [
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

export type CategoryKey = (typeof CATEGORIES)[number]["key"];

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
