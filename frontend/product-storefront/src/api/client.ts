/**
 * Typed Storefront API client.
 *
 * **Fixtures by default, because there is no storefront service yet.** Nothing on the gateway
 * serves themes, store documents or this catalogue on this branch — that is expected at this stage
 * and stated here rather than papered over. The whole app therefore renders from `fixtures.ts` and
 * the theme library with no gateway and no login. Set `VITE_USE_FIXTURES=false` once the service
 * ships and every call switches to the gateway at the same-origin `/api/v1` prefix (Vite proxies
 * it to `http://localhost:8100` in dev) with the bearer token from the session — the live paths are
 * written out below already, so going live is a flag flip and not a rewrite.
 */

import { apiRequest } from "@opsgrids/client";

import { adoptTheme as forkTheme, resolveMode } from "@/theme/store";
import type { StoreDoc, Theme } from "@/theme/schema";
import { quickcart, suggestedBrand, themeByKey, THEMES } from "@/theme/themes";
import { CATEGORY_LIST, PRODUCTS, RETAIL_CATEGORY_LIST, RETAIL_PRODUCTS } from "./fixtures";
import type { Category, Page, Product, StoreResponse } from "./types";

/** Default source. Fixtures unless `VITE_USE_FIXTURES=false` pins the app to the live gateway.
 *  Dev builds only: `import.meta.env.DEV` compiles to `false` in production, so a stray
 *  CI/production env var can never ship fixture mode — an app rendering fabricated data
 *  with the "Sample data" markers suppressed. */
export const USE_FIXTURES = import.meta.env.DEV && import.meta.env.VITE_USE_FIXTURES !== "false";

/** True when the app is talking to the real gateway. */
export const LIVE = !USE_FIXTURES;

/** A generous page size — one request returns the whole (small) catalogue for this org. */
const FULL_PAGE = 200;

/** Small artificial delay so loading states are exercised in fixtures mode. */
function settle<T>(value: T, ms = 240): Promise<T> {
  return new Promise((resolve) => setTimeout(() => resolve(value), ms));
}

/* --------------------------------- themes -------------------------------- */

/**
 * The shipped theme library. It is a build artefact of this app rather than a resource on the
 * wire: a theme is code (its sections are components), so there is no version of this that a
 * server usefully serves.
 */
export async function listThemes(): Promise<Theme[]> {
  return settle(THEMES, 120);
}

/* --------------------------------- store --------------------------------- */

/**
 * The merchant's store document, in session.
 *
 * Fixture mode starts with a theme already adopted so the buyer-facing view is reachable without
 * a click; "Remove this store" on the gallery clears it back to `null`, which is the state a
 * merchant who has just activated the product is genuinely in.
 *
 * The pre-adopted theme is `quickcart`, paired with *its own* suggested brand. `adoptTheme` keeps
 * whatever brand the store already has, which is right for a real merchant re-theming and wrong
 * for a seed — forking here rather than calling `adoptTheme` is what keeps both true.
 */
let currentStore: StoreDoc | null = forkTheme(quickcart, suggestedBrand(quickcart.key));

export async function getStore(): Promise<StoreResponse> {
  if (USE_FIXTURES) return settle(currentStore);
  return apiRequest<StoreResponse>(`/storefront/store`);
}

/**
 * Adopt a theme: fork it into a store document.
 *
 * Re-adopting keeps the merchant's brand and replaces tokens and pages — the merchant's edits to
 * the old pages are gone, which is what "adopting a different theme replaces the pages" means and
 * why the gallery asks before it does this.
 */
export async function adoptTheme(themeKey: string): Promise<StoreDoc> {
  const theme = themeByKey(themeKey);
  if (!theme) throw new Error("That theme is no longer available.");
  const brand = currentStore?.brand ?? suggestedBrand(themeKey);
  const next = forkTheme(theme, brand);

  if (USE_FIXTURES) {
    currentStore = next;
    return settle(next, 320);
  }
  // The server forks the theme itself — the client never uploads a whole document to adopt one.
  return apiRequest<StoreDoc>(`/storefront/store/adopt`, {
    method: "POST",
    body: { theme_key: themeKey },
  });
}

/** Persist an edited store document. The editor calls this; the renderer only reads. */
export async function saveStore(store: StoreDoc): Promise<StoreDoc> {
  if (USE_FIXTURES) {
    currentStore = store;
    return settle(store, 200);
  }
  return apiRequest<StoreDoc>(`/storefront/store`, { method: "PUT", body: store });
}

/** Clear the store back to "no theme adopted". */
export async function removeStore(): Promise<null> {
  if (USE_FIXTURES) {
    currentStore = null;
    return settle(null, 200);
  }
  return apiRequest<null>(`/storefront/store`, { method: "DELETE" });
}

/* ------------------------------- catalogue -------------------------------- */

/**
 * Which fixture catalogue the adopted store should see.
 *
 * **Fixture-only.** A real storefront service returns the merchant's own catalogue and has no
 * notion of a "retail slice" — a grocer's stock is a grocer's stock whatever theme they picked.
 * This exists because one shared fixture catalogue would put a quick-commerce theme in front of
 * `FST-HX-M12 Hex bolt M12 × 60` under a "27% OFF" flag, which is not a demo anyone can defend.
 *
 * With no store adopted the merchant is on the gallery, where the trade catalogue is the safe
 * default: `resolveMode` narrows anything unrecognised to trade for the same reason.
 */
function fixtureSlice(): { products: Product[]; categories: Category[] } {
  const retail = currentStore !== null && resolveMode(currentStore) === "retail";
  return retail
    ? { products: RETAIL_PRODUCTS, categories: RETAIL_CATEGORY_LIST }
    : { products: PRODUCTS, categories: CATEGORY_LIST };
}

export async function listProducts(): Promise<Page<Product>> {
  if (USE_FIXTURES) {
    const { products } = fixtureSlice();
    return settle({ items: products, total: products.length });
  }
  return apiRequest<Page<Product>>(`/storefront/products?size=${FULL_PAGE}`);
}

export async function listCategories(): Promise<Category[]> {
  if (USE_FIXTURES) return settle(fixtureSlice().categories);
  return apiRequest<Category[]>(`/storefront/categories`);
}

/* -------------------------------- enquiries ------------------------------- */

export interface EnquiryInput {
  name: string;
  company: string;
  email: string;
  phone: string;
  requirement: string;
  quantity: string;
  gstin: string;
}

export interface EnquiryReceipt {
  id: string;
  received_at: string;
}

/**
 * A buyer's enquiry.
 *
 * The form validates before it calls this, and this validates again — client validation mirrors
 * the server's rules and never replaces them. When the service exists the same rejection arrives
 * as an RFC 7807 problem document; the shape of the failure the form handles does not change.
 */
export async function submitEnquiry(input: EnquiryInput): Promise<EnquiryReceipt> {
  const missing =
    input.name.trim() === "" ||
    input.company.trim() === "" ||
    input.email.trim() === "" ||
    input.requirement.trim() === "";
  if (missing) throw new Error("Some required details are missing. Check the fields above.");

  if (USE_FIXTURES) {
    return settle({ id: `enq-${Date.now().toString(36)}`, received_at: new Date().toISOString() }, 480);
  }
  return apiRequest<EnquiryReceipt>(`/storefront/enquiries`, { method: "POST", body: input });
}
