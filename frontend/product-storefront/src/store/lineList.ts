/**
 * The in-page line list — what ADD adds to.
 *
 * **Scope, stated in the code because a demo becomes a promise otherwise.** This is a `useState`
 * held by one React provider and nothing else. Pressing ADD adds a line, steps it up and down,
 * moves the count in the announcement bar and says so to a screen reader. It does **not** survive a
 * reload, reach a server, produce an order, reserve stock or take payment. There is no cart route
 * and no checkout on this build, which is why the count in `AnnouncementBar` is text and not a
 * link: a control that looks like a destination and goes nowhere costs a buyer more than it is
 * worth.
 *
 * The two modes are not symmetric, and the asymmetry is worth knowing. Trade's list has a real
 * terminus already — `submitEnquiry()` exists and validates — so "send this list as an enquiry" is
 * an end-to-end journey with no new backend behind it. Retail's ADD stops at the count.
 *
 * **The reducer is pure and lives above the provider**, the way `editor/document.ts` does it, so
 * every rule here is exercised in `lineList.test.ts` with no DOM: quantities are arithmetic, and
 * arithmetic you can only reach by clicking is arithmetic nobody checks.
 *
 * **`useLineList()` returns a working no-op when no provider is mounted.** That is the whole trick
 * and not a convenience: the gallery's miniatures, the editor's canvas and every test that renders
 * a card on its own keep working untouched — ADD paints, pressing it does nothing, nothing throws.
 * `StoreRenderer` is the only place the real provider is mounted.
 */

import { createContext, useContext } from "react";

import type { Product, ProductVariant } from "@/api/types";

/**
 * One line, keyed by the pack the buyer actually chose.
 *
 * The price is **copied in at the moment of adding** rather than looked up later, because the
 * provider has no catalogue: it is handed lines, not products. A price that changed under a buyer
 * mid-session would silently restate a total they had already read, so the number they were shown
 * is the number that totals.
 */
export interface LineListEntry {
  /** `lineKey(productId, variantId)` — the map key, repeated here so an entry stands alone. */
  key: string;
  productId: string;
  /** The chosen pack, or `null` for a line with no selector. Part of the identity, not a detail. */
  variantId: string | null;
  /** What to call this line in a summary — the product, and the pack when one was chosen. */
  name: string;
  /** Minor units, always. Money is integer minor units with an ISO currency, platform-wide. */
  unitPriceMinor: number;
  currency: Product["currency"];
  qty: number;
}

/** Everything needed to open a line, except how many. */
export type LineDraft = Omit<LineListEntry, "key" | "qty">;

/** Insertion-ordered, so a summary lists lines in the order the buyer built them. */
export type LineListState = ReadonlyMap<string, LineListEntry>;

/** The empty list, shared — an identity the reducer can return to say "nothing changed". */
export const EMPTY_LINE_LIST: LineListState = new Map<string, LineListEntry>();

/**
 * The identity of a line.
 *
 * A product with no chosen pack and the same product at 1 kg are different lines, so the variant is
 * in the key. `null` and `""` collapse to the same key on purpose: a single-pack line has no
 * meaningful variant and must not be able to open two entries depending on which of the two a
 * caller passed.
 */
export function lineKey(productId: string, variantId: string | null): string {
  return `${productId}:${variantId ?? ""}`;
}

/**
 * The line a card would open for this product and pack.
 *
 * The **low end of the price band** is the unit price, because that is the number the card printed:
 * `formatPriceBand` shows "from ₹x" for a band, and a list that totalled the top of the band would
 * quietly disagree with what the buyer just read.
 */
export function lineDraft(product: Product, variant: ProductVariant | null): LineDraft {
  return {
    productId: product.id,
    variantId: variant?.id ?? null,
    name: variant ? `${product.name} · ${variant.label}` : product.name,
    unitPriceMinor: variant ? variant.price_min_minor : product.price_min_minor,
    currency: product.currency,
  };
}

export type LineListAction =
  | { type: "add"; line: LineDraft; qty: number }
  | { type: "set-qty"; key: string; qty: number }
  | { type: "remove"; key: string }
  | { type: "clear" };

/**
 * Every rule the list has, as one pure function.
 *
 * Two invariants it keeps, both of which are bugs everywhere they are not kept:
 *
 * - **A quantity at or below zero is a removal**, never a line sitting at 0 or −1. A stepper that
 *   can reach zero and a list that can hold zero would show "0" as a state a buyer has to clear.
 * - **State identity only changes when the state does.** Removing a key that is not there, or
 *   clearing an empty list, returns the same map, so a no-op press cannot re-render the store.
 */
export function lineListReducer(state: LineListState, action: LineListAction): LineListState {
  switch (action.type) {
    case "add": {
      if (action.qty <= 0) return state;
      const key = lineKey(action.line.productId, action.line.variantId);
      const existing = state.get(key);
      const next = new Map(state);
      /* Re-adding takes the *current* draft's price and name: the buyer moved the pack selector,
         and the entry has to follow it rather than keep the price it was opened with. */
      next.set(key, { ...action.line, key, qty: (existing?.qty ?? 0) + action.qty });
      return next;
    }
    case "set-qty": {
      const existing = state.get(action.key);
      if (!existing) return state;
      if (action.qty <= 0) {
        const next = new Map(state);
        next.delete(action.key);
        return next;
      }
      if (existing.qty === action.qty) return state;
      const next = new Map(state);
      next.set(action.key, { ...existing, qty: action.qty });
      return next;
    }
    case "remove": {
      if (!state.has(action.key)) return state;
      const next = new Map(state);
      next.delete(action.key);
      return next;
    }
    case "clear":
      return state.size === 0 ? state : EMPTY_LINE_LIST;
  }
}

/** How many distinct lines — what a trade buyer means by "4 lines". */
export function lineCount(state: LineListState): number {
  return state.size;
}

/**
 * How many units in total — what a retail buyer means by "4 items".
 *
 * Distinct from `lineCount` because the two vocabularies genuinely count different things: three
 * packets of butter is one line and three items, and a trade line at MOQ 500 is one line and not
 * "500 items".
 */
export function lineUnits(state: LineListState): number {
  let units = 0;
  for (const entry of state.values()) units += entry.qty;
  return units;
}

/** The list's value in minor units. Nothing here divides by 100 — `lib/format` does that. */
export function lineTotalMinor(state: LineListState): number {
  let total = 0;
  for (const entry of state.values()) total += entry.qty * entry.unitPriceMinor;
  return total;
}

/** What a card or a bar gets back from `useLineList()`. */
export interface LineListApi {
  lines: LineListState;
  /** Distinct lines. */
  count: number;
  /** Total units across every line. */
  units: number;
  totalMinor: number;
  /** The quantity currently held against a `lineKey`, or 0. */
  qtyOf: (key: string) => number;
  add: (line: LineDraft, qty: number) => void;
  setQty: (key: string, qty: number) => void;
  remove: (key: string) => void;
  clear: () => void;
}

function ignore(): void {
  /* No provider: the caller is a miniature, a canvas or a test, and there is nothing to add to. */
}

/**
 * The value when nobody provides one.
 *
 * A module constant rather than a fresh object per read, so a consumer outside a provider gets a
 * stable identity and cannot be re-rendered by its own hook.
 */
const NO_LINE_LIST: LineListApi = {
  lines: EMPTY_LINE_LIST,
  count: 0,
  units: 0,
  totalMinor: 0,
  qtyOf: () => 0,
  add: ignore,
  setQty: ignore,
  remove: ignore,
  clear: ignore,
};

/**
 * The one context a section may read.
 *
 * Its default is the no-op above rather than `undefined`, which is why `useLineList()` never throws
 * and why no caller needs a provider to render. See the rule in `theme/render/types.ts`.
 *
 * The provider that fills it is `LineListProvider.tsx`, which is the only component in this folder.
 * Splitting it off keeps this module free of JSX — the reducer and its arithmetic are testable with
 * no DOM, the way `editor/document.ts` is — and keeps the provider file exporting nothing but a
 * component, which is what fast refresh needs to reload it cleanly.
 */
export const LineListContext = createContext<LineListApi>(NO_LINE_LIST);

/**
 * The list, or a working no-op when there is no provider above.
 *
 * Callers must not branch on which one they got — that is the point. A card that behaves
 * differently inside and outside a provider is a card the editor's canvas renders differently from
 * the store, and the whole renderer is built so those two are the same tree.
 */
export function useLineList(): LineListApi {
  return useContext(LineListContext);
}
