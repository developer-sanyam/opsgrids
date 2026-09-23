/**
 * Typed Storefront API client — the theme document and the merchant's catalogue.
 *
 * **Fixtures by default in dev.** `USE_FIXTURES` is `true` only when `import.meta.env.DEV` and
 * `VITE_USE_FIXTURES` is not explicitly `"false"`, so a production build always talks to the real
 * gateway at the same-origin `/api/v1` prefix (Vite proxies it to the gateway in dev) with the
 * bearer token from the session. `services/storefront` is real and routed
 * (`infrastructure/edge/routes.prod.json`); this module's live branches are what a merchant's
 * console actually calls.
 *
 * Enquiries and serviceability are anonymous, buyer-facing calls and live in
 * `@/api/publicClient` instead — that module carries none of `@opsgrids/client`, the theme
 * library or the fixture catalogue, which the buyer bundle must not ship (see its header).
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
import { getStoreIdentity } from "@/settings/api";
import { CATEGORY_LIST, PRODUCTS, RETAIL_CATEGORY_LIST, RETAIL_PRODUCTS } from "./fixtures";
import { LIVE, USE_FIXTURES } from "./mode";
import type { Asset, Category, Page, Product, StoreResponse } from "./types";

/**
 * The buyer's bootstrap — "which shop is this address?" (ADR-0012 §2).
 *
 * **Implemented in `./storeContext`, not here, and that is load-bearing.** This module imports
 * `@opsgrids/client`, the theme library and the fixture catalogue; the buyer bundle must carry
 * none of the three. Re-exported so the console reaches it through `@/api` like everything else,
 * while `src/buyer/*` imports `@/api/storeContext` directly and pulls in nothing above this line.
 */
export {
  currentHost,
  loadStoreContext,
  readCachedStoreContext,
  StoreContextError,
  STOREFRONT_API_BASE,
} from "./storeContext";
export type {
  PublicStoreContextIn,
  StoreContextFailure,
  StoreContextOut,
} from "./storeContext";

/** Fixture mode. Defined in `./mode`, which imports nothing, so `@/settings/api` can read it
 *  without importing this module back (see that file). Re-exported for every existing caller. */
export { LIVE, USE_FIXTURES } from "./mode";

/** A generous page size — one request returns the whole (small) catalogue for the theme
 *  renderer's own preview. The dedicated Products/Categories screens page for real. */
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

/* ----------------------------- theme document ----------------------------- */

/**
 * The theme document — `GET/PUT/DELETE /storefront/theme` (ADR-0011 §3). **PUT is adopt**: there
 * is no separate `/adopt` route, so `adoptTheme` forks the theme client-side (as it always did)
 * and PUTs the result. `brand` is not part of this document (ADR-0011 §2) — it lives on the store
 * identity (`StoreOut.name/tagline/logo_text`), so composing a `StoreDoc` for the renderer means
 * reading both resources and merging them, done once here so every screen keeps calling
 * `getStore()` and getting a `StoreDoc` back exactly as before.
 *
 * **Optimistic concurrency.** `version` travels with every save and a stale one is a 409
 * `theme_version_conflict` — the server's own detail already tells the merchant to reload, and
 * `EditorShell` renders it with a "Reload" action rather than "Try again".
 */
let currentStore: StoreDoc | null = forkTheme(quickcart, suggestedBrand(quickcart.key));
/** The version the document was last read or saved at. `null` until a live read or save happens;
 *  irrelevant in fixture mode, which holds no server-side row to conflict with. */
let currentVersion: number | null = null;

interface ThemeDocOut {
  theme_key: string;
  mode: string;
  tokens: Record<string, unknown>;
  pages: unknown[];
  version: number;
}

/** Compose the renderer's `StoreDoc` from the theme document and the store's own brand fields. */
async function composeStoreDoc(doc: ThemeDocOut): Promise<StoreDoc> {
  const identity = await getStoreIdentity().catch(() => null);
  const brand = identity
    ? { name: identity.name, tagline: identity.tagline ?? "", logoText: identity.logo_text ?? "" }
    : suggestedBrand(doc.theme_key);
  return {
    themeKey: doc.theme_key,
    mode: doc.mode === "retail" ? "retail" : "trade",
    brand,
    tokens: doc.tokens as unknown as StoreDoc["tokens"],
    pages: doc.pages as unknown as StoreDoc["pages"],
  };
}

export async function getStore(): Promise<StoreResponse> {
  if (USE_FIXTURES) return settle(currentStore);
  try {
    const doc = await apiRequest<ThemeDocOut>("/storefront/theme");
    currentVersion = doc.version;
    return await composeStoreDoc(doc);
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) {
      currentVersion = null;
      return null;
    }
    throw error;
  }
}

/**
 * Adopt a theme: fork it into a store document and PUT it — this *is* adopt (ADR-0011 §3). Keeps
 * the merchant's existing brand where one is on record; a first adopt has none yet, so it starts
 * from the theme's own suggested brand, which the merchant edits on `/settings`.
 */
export async function adoptTheme(themeKey: string): Promise<StoreDoc> {
  const theme = themeByKey(themeKey);
  if (!theme) throw new Error("That theme is no longer available.");

  if (USE_FIXTURES) {
    const brand = currentStore?.brand ?? suggestedBrand(themeKey);
    const next = forkTheme(theme, brand);
    currentStore = next;
    return settle(next, 320);
  }

  const identity = await getStoreIdentity().catch(() => null);
  const brand = identity
    ? { name: identity.name, tagline: identity.tagline ?? "", logoText: identity.logo_text ?? "" }
    : suggestedBrand(themeKey);
  const next = forkTheme(theme, brand);
  const saved = await apiRequest<ThemeDocOut>("/storefront/theme", {
    method: "PUT",
    body: {
      theme_key: next.themeKey,
      mode: next.mode,
      tokens: next.tokens,
      pages: next.pages,
      version: currentVersion ?? 1,
    },
  });
  currentVersion = saved.version;
  return next;
}

/** Persist an edited store document. The editor calls this; the renderer only reads. */
export async function saveStore(store: StoreDoc): Promise<StoreDoc> {
  if (USE_FIXTURES) {
    currentStore = store;
    return settle(store, 200);
  }
  const saved = await apiRequest<ThemeDocOut>("/storefront/theme", {
    method: "PUT",
    body: {
      theme_key: store.themeKey,
      mode: store.mode,
      tokens: store.tokens,
      pages: store.pages,
      version: currentVersion ?? 1,
    },
  });
  currentVersion = saved.version;
  return store;
}

/** Remove the store's theme document — "Remove this store" in the gallery. Soft delete: a later
 *  adopt starts fresh at `version: 1`. Idempotent: removing an already-removed document is a
 *  no-op rather than a surfaced 404. */
export async function removeStore(): Promise<null> {
  if (USE_FIXTURES) {
    currentStore = null;
    return settle(null, 200);
  }
  try {
    await apiRequest<void>("/storefront/theme", { method: "DELETE" });
  } catch (error) {
    if (!(error instanceof ApiError && error.status === 404)) throw error;
  }
  currentVersion = null;
  return null;
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

/**
 * The theme renderer's own catalogue read — everything active, for composing a store preview.
 * The dedicated `/products` and `/categories` console screens page, filter and sort for real
 * (`@/catalogue/api`); this stays a flat list because a page's sections need "the catalogue",
 * not a page of it.
 */
export async function listProducts(): Promise<Page<Product>> {
  if (USE_FIXTURES) {
    const { products } = fixtureSlice();
    return settle({ items: products, total: products.length });
  }
  return apiRequest<Page<Product>>(`/storefront/products?size=${FULL_PAGE}`);
}

export async function listCategories(): Promise<Category[]> {
  if (USE_FIXTURES) return settle(fixtureSlice().categories);
  const page = await apiRequest<Page<Category>>(`/storefront/categories?size=${FULL_PAGE}`);
  return page.items;
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

/**
 * What live mode says once it has seen the document service 404 — the known infrastructure gap:
 * `services/document` and its object store are not deployed on the production box
 * (`infrastructure/edge/compose.prod.yml` has neither), so every upload fails the same way every
 * time. Told once, in words, rather than left as a spinner that always ends in the same error.
 */
export const UPLOAD_SERVICE_UNDEPLOYED =
  "Image uploads aren't available yet on this box. Paste an image address (https://…) instead — this starts working on its own once the upload service is deployed.";

/** Set the first time a live upload 404s, so the control can stop offering a button that can only
 *  fail for the rest of the session. Never reset by a later success — if the route reappears, a
 *  reload picks it up, which is true of every other route this app calls. */
let uploadServiceConfirmedMissing = false;

export function isUploadServiceUnavailable(): boolean {
  return LIVE && uploadServiceConfirmedMissing;
}

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
    if (res.status === 404) uploadServiceConfirmedMissing = true;
    const problem = await res
      .json()
      .then((body: ProblemJson) => body)
      .catch(() => null);
    // The server's own detail is the specific one — it names the quota, the limit, the format.
    throw new ApiError(
      res.status,
      problem?.detail ?? (res.status === 404 ? UPLOAD_SERVICE_UNDEPLOYED : uploadFallbackMessage(res.status)),
      problem,
    );
  }

  return (await res.json()) as Asset;
}
