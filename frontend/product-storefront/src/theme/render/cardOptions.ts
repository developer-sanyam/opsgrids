/**
 * The options a product card is drawn with.
 *
 * One shape, shared by the card and by every section that renders cards, so a merchant setting
 * spelled in `product-grid` and the same setting spelled in `product-carousel` cannot drift apart.
 * Kept deliberately small: a section reads its settings once through `readCardOptions` and hands
 * the result straight to `ProductCard`.
 *
 * None of these can make a trade store claim a retail promise. `showBadge` and `showEta` are
 * permissions, not instructions — the card still requires `mode === "retail"` *and* non-null data
 * before either one paints.
 */

import type { Product } from "@/api/types";
import type { StoreMode } from "../schema";
import type { ActionSize } from "./primitives";
import { bool, choice, type Settings } from "./settings";

/** `detail` carries the spec line; `compact` drops it and tightens padding, for the phone rails. */
export const CARD_STYLES = ["detail", "compact"] as const;
export type CardStyle = (typeof CARD_STYLES)[number];

/** How the price is qualified. Overrides the product's own GST basis for a whole section. */
export const PRICE_NOTES = ["exclusive", "inclusive", "on-request"] as const;
export type PriceNote = (typeof PRICE_NOTES)[number];

export interface CardOptions {
  cardStyle: CardStyle;
  /** Allow the computed discount flag. Retail plus a real saving still has to hold. */
  showBadge: boolean;
  /** Allow the delivery-time chip. Retail plus a non-null ETA still has to hold. */
  showEta: boolean;
  /** Allow the pack-size selector. Two or more variants still has to hold. */
  showVariants: boolean;
  priceNote: PriceNote;
  /** The size of the card's primary action. `sm` is the compact retail ADD. */
  size: ActionSize;
}

export const DEFAULT_CARD_OPTIONS: CardOptions = {
  cardStyle: "detail",
  showBadge: true,
  showEta: true,
  showVariants: true,
  priceNote: "exclusive",
  size: "md",
};

/**
 * Card options out of a section's settings bag, with anything the section decides itself — the
 * action size, say, which is a layout choice and not a merchant setting — passed as an override.
 */
export function readCardOptions(settings: Settings, overrides: Partial<CardOptions> = {}): CardOptions {
  return {
    cardStyle: choice(settings, "cardStyle", CARD_STYLES, DEFAULT_CARD_OPTIONS.cardStyle),
    showBadge: bool(settings, "showBadge", DEFAULT_CARD_OPTIONS.showBadge),
    showEta: bool(settings, "showEta", DEFAULT_CARD_OPTIONS.showEta),
    showVariants: bool(settings, "showVariants", DEFAULT_CARD_OPTIONS.showVariants),
    priceNote: choice(settings, "priceNote", PRICE_NOTES, DEFAULT_CARD_OPTIONS.priceNote),
    size: DEFAULT_CARD_OPTIONS.size,
    ...overrides,
  };
}

/**
 * How much one press of a quantity control moves the line.
 *
 * A retail buyer adds one packet at a time; a trade buyer cannot order below the minimum order
 * quantity, so their first press is a full MOQ. Pure, and exported here rather than from the card
 * so the line list can share it without importing a component.
 */
export function stepQuantity(product: Product, mode: StoreMode): number {
  return mode === "retail" ? 1 : Math.max(1, product.moq);
}
