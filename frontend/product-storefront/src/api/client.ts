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

import {
  API_BASE,
  ApiError,
  apiRequest,
  getAccessToken,
  notifyUnauthorized,
  type ProblemJson,
} from "@opsgrids/client";

import { adoptTheme as forkTheme, resolveMode } from "@/theme/store";
import type { StoreDoc, Theme } from "@/theme/schema";
import { quickcart, suggestedBrand, themeByKey, THEMES } from "@/theme/themes";
import { CATEGORY_LIST, PRODUCTS, RETAIL_CATEGORY_LIST, RETAIL_PRODUCTS } from "./fixtures";
import type { Asset, Category, Page, Product, StoreResponse } from "./types";

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

/* --------------------------------- assets --------------------------------- */

/**
 * Image upload, against `services/document` — a **platform** route (`/api/v1/documents`), not a
 * storefront one, so it is live even while everything above this line is fixtures. It is the one
 * call in this file that carries bytes, which is why it does not go through `apiRequest`: that
 * helper JSON-encodes its body and sets `Content-Type: application/json`, and a multipart body
 * needs the browser to set the header itself so the boundary matches.
 */

/** 5 MiB, matching the server's limit exactly (ADR-0009 §5). Checked here so the common mistake
 *  is caught instantly instead of after a five-megabyte round trip — never *instead of* there. */
export const MAX_ASSET_BYTES = 5 * 1024 * 1024;

/** The three formats the server accepts. Anything else is refused, and the bytes decide there. */
export const ACCEPTED_ASSET_TYPES = ["image/jpeg", "image/png", "image/webp"] as const;

/** For the file input's `accept`. A filter in the picker, never a check — it is trivially bypassed. */
export const ASSET_ACCEPT = ACCEPTED_ASSET_TYPES.join(",");

export const ASSET_TOO_LARGE =
  "That image is over 5 MB. Export it at a smaller size or a lower quality and try again.";

/** SVG is refused by the server permanently — it is a script container (ADR-0009 §5), so this is
 *  not a limit that will be raised and the copy says so rather than inviting a retry. */
export const ASSET_SVG_REFUSED =
  "SVG files can't be uploaded. Export the artwork as a PNG or WebP and upload that.";

export const ASSET_WRONG_TYPE = "Only JPEG, PNG and WebP images can be uploaded.";

/**
 * What fixtures mode does instead of uploading: **it refuses, and says why.**
 *
 * The alternative — hand back a `blob:` URL and let the editor look like it worked — writes an
 * address into the store document that dies with the tab, renders for nobody else, and fails on
 * reload with no explanation. A refusal costs the merchant one message; a fake success costs them
 * the time it takes to work out that their store is broken. Fixtures mode is dev-only
 * (`USE_FIXTURES` requires `import.meta.env.DEV`), so the audience for this sentence is an
 * engineer and it names the flag.
 */
export const UPLOAD_NEEDS_SERVICE =
  "Uploads need the document service, and this app is running on sample data. Paste an image address instead, or set VITE_USE_FIXTURES=false to run against the gateway.";

/** How long to wait before giving up. The server's own budget for 5 MiB is 20s (ADR-0009 §10);
 *  this sits beyond it so a slow-but-working upload is never cut off by the client first. */
const UPLOAD_TIMEOUT_MS = 30_000;

function megabytes(bytes: number): string {
  return (bytes / (1024 * 1024)).toFixed(1);
}

/**
 * The client-side half of validation — mirroring the server's rules, never replacing them.
 *
 * Returns the sentence to show, or `null` when the file is worth sending. An empty `type` is *not*
 * refused: some platforms report nothing for a perfectly good JPEG, and the server decides from
 * the decoded bytes anyway. Guessing here would refuse a file the server would have accepted.
 */
export function refuseAssetFile(file: File): string | null {
  const type = file.type.toLowerCase();
  const name = file.name.toLowerCase();

  if (type === "image/svg+xml" || name.endsWith(".svg")) return ASSET_SVG_REFUSED;
  if (type !== "" && !(ACCEPTED_ASSET_TYPES as readonly string[]).includes(type)) {
    return ASSET_WRONG_TYPE;
  }
  if (file.size > MAX_ASSET_BYTES) {
    return `${ASSET_TOO_LARGE} This one is ${megabytes(file.size)} MB.`;
  }
  return null;
}

/** What to say when the server refused and sent no problem document to say it better. */
function uploadFallbackMessage(status: number): string {
  if (status === 401) return "Your session has expired. Sign in again and retry the upload.";
  if (status === 403) return "You don't have permission to upload images. An owner or admin can grant it.";
  if (status === 413) return ASSET_TOO_LARGE;
  if (status === 415 || status === 422) return ASSET_WRONG_TYPE;
  if (status === 429) return "Too many uploads at once. Wait a moment and try again.";
  if (status >= 500) return "The upload service isn't responding. Try again in a moment.";
  return `The upload failed (${status}).`;
}

/**
 * Upload one image and get back the asset whose `public_path` goes into the setting.
 *
 * The caller writes `asset.public_path` — a root-relative `/a/{org}/{id}` — into the document, and
 * `resolveImageSrc` puts a host in front of it at render time. No absolute URL is ever stored.
 */
export async function uploadAsset(file: File): Promise<Asset> {
  const refusal = refuseAssetFile(file);
  if (refusal !== null) throw new Error(refusal);

  if (USE_FIXTURES) throw new Error(UPLOAD_NEEDS_SERVICE);

  const form = new FormData();
  form.append("file", file, file.name);

  const headers: Record<string, string> = { Accept: "application/json" };
  const token = getAccessToken();
  if (token !== null) headers.Authorization = `Bearer ${token}`;

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), UPLOAD_TIMEOUT_MS);

  let res: Response;
  try {
    res = await fetch(`${API_BASE}/documents/assets`, {
      method: "POST",
      headers,
      body: form,
      signal: controller.signal,
    });
  } catch {
    // An abort and a dead network are indistinguishable to the merchant, and the remedy is the
    // same sentence. Nothing was written — the server's transaction rolls back (ADR-0009 §9).
    throw new Error(
      controller.signal.aborted
        ? "The upload timed out. Check your connection and try again — nothing was saved."
        : "Couldn't reach the server. Check your connection and try again.",
    );
  } finally {
    clearTimeout(timer);
  }

  if (!res.ok) {
    if (res.status === 401) notifyUnauthorized();
    const problem = await res
      .json()
      .then((body: ProblemJson) => body)
      .catch(() => null);
    // The server's own detail is the specific one — it names the quota, the limit, the format.
    throw new ApiError(res.status, problem?.detail ?? uploadFallbackMessage(res.status), problem);
  }

  return (await res.json()) as Asset;
}
