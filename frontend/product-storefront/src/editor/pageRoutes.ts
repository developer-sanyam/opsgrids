/**
 * What a page's address may be, and which pages may not change theirs.
 *
 * A store's routes are a namespace with no server behind it: `pageForRoute` is an exact string
 * match, `liveRoutes.storeRouteForPath` hands the URL straight through, and nothing anywhere
 * rejects a second page at `/about`. So the rules have to live somewhere, once, callable — the
 * reducer enforces them and the page manager shows them, and neither gets its own copy.
 *
 * Everything here is pure and string-in, string-out, so `pageRoutes.test.ts` can pin the whole
 * rule set without a document, a DOM or a router.
 */

import type { PageDoc } from "@/theme/schema";

/** The store's entry point. There is exactly one and it never moves. */
export const HOME_ROUTE = "/";

/**
 * The three routes a theme ships, and the only three the renderer or a section can name without
 * the merchant having written the link.
 *
 * **These cannot be deleted or re-routed.** Not because the editor is squeamish about destructive
 * acts — it deletes sections all day — but because each one is a link target something else
 * resolves *implicitly*, and the breakage lands somewhere the merchant was not looking:
 *
 * - `/` is the entry point. A store whose home page is gone answers its own domain with the
 *   renderer's "no page at that address" panel.
 * - `/catalogue` is written by `CategoryTiles`, which links every tile to
 *   `/store/catalogue?category=…` with no check that the page exists. Delete the page and every
 *   category tile on the home page becomes a dead link, from an action taken on a different page.
 * - `/product` is the line detail page. The document has no field naming "the page a line opens
 *   on", so the route *is* the name; re-routing it would need a store-level setting that does not
 *   exist yet.
 *
 * The merchant's actual wish — "I don't sell from a catalogue, get it out of my navigation" — is
 * served by `nav: "none"`, which this unit adds. Hiding is available; removing is not. All three
 * become ordinary pages the day the document carries explicit link targets instead of routes
 * hard-coded in section code.
 */
export const STRUCTURAL_ROUTES = ["/", "/catalogue", "/product"] as const;

/**
 * The console's own top-level paths.
 *
 * Today a store page is namespaced (`/store/about`, `/live/about`) so none of these can actually
 * collide. They are refused anyway because the whole point of `/live` is that it is a stand-in for
 * the store's own domain, where the store's routes sit at the root and `/editor` would shadow the
 * merchant's way back into the console. Refusing six words now is free; migrating a merchant's
 * published `/settings` page later is not.
 */
export const RESERVED_ROUTES = ["/editor", "/help", "/live", "/login", "/settings", "/store"];

/** Long enough for `/company/investor-relations`, short enough to stay a URL people can say. */
export const MAX_ROUTE_LENGTH = 120;

/** Long enough to be a nav item and a heading; anything longer is a page, not a page name. */
export const MAX_PAGE_NAME_LENGTH = 60;

/**
 * The segments an input reduces to, after every normalisation this module is willing to perform.
 *
 * Normalise rather than reject wherever the intent is unambiguous: a merchant typing `About Us`
 * means `/about-us`, and refusing them a capital letter teaches nothing. Only input that survives
 * none of this — `?????`, `…` — is a real error.
 */
function segmentsOf(input: string): string[] {
  return input
    // `café` → `cafe` rather than `caf`: decompose, then drop the combining marks.
    .normalize("NFD")
    .replace(/[̀-ͯ]/g, "")
    .trim()
    .toLowerCase()
    // Spaces and underscores are what a merchant types where a hyphen belongs.
    .replace(/[\s_]+/g, "-")
    .replace(/[^a-z0-9\-/]+/g, "")
    .split("/")
    .map((segment) => segment.replace(/-+/g, "-").replace(/^-+|-+$/g, ""))
    .filter((segment) => segment !== "");
}

/**
 * The address an input becomes: lowercase, hyphenated, leading slash, no trailing slash.
 *
 * Multi-segment routes are allowed — `/company/careers` is exactly what a 120-page sitemap wants,
 * the live splat already carries it and `pageForRoute` already matches it. An input holding
 * nothing usable normalises to `"/"`, which is then refused as a duplicate of the home page; use
 * `checkRoute` rather than reading this on its own.
 */
export function normaliseRoute(input: string): string {
  const segments = segmentsOf(input);
  return segments.length === 0 ? HOME_ROUTE : `/${segments.join("/")}`;
}

/** A page name, trimmed and collapsed. Never empty — `nameFromRoute` is the fallback. */
export function normalisePageName(input: string): string {
  return input.replace(/\s+/g, " ").trim().slice(0, MAX_PAGE_NAME_LENGTH);
}

/** `/about-us` → `About us`. What the name field pre-fills with while the merchant types. */
export function nameFromRoute(route: string): string {
  const last = segmentsOf(route).at(-1);
  if (last === undefined) return "Home";
  const words = last.replace(/-/g, " ");
  return words.charAt(0).toUpperCase() + words.slice(1);
}

export function isStructuralRoute(route: string): boolean {
  return (STRUCTURAL_ROUTES as readonly string[]).includes(route);
}

/**
 * Why this page's address is locked, in the merchant's words — or null when it is not.
 *
 * The same sentence is the reducer's reason for refusing and the field's helper text, so the
 * editor never disables a control it cannot explain.
 */
export function structuralReason(route: string): string | null {
  if (route === "/") {
    return "Every store needs a home page, so this one keeps its address and can't be deleted.";
  }
  if (route === "/catalogue") {
    return "Category tiles link to /catalogue, so this page keeps its address. Set its navigation to “Not in the navigation” to hide it from buyers.";
  }
  if (route === "/product") {
    return "Product sections open a line on /product, so this page keeps its address. Set its navigation to “Not in the navigation” to hide it from buyers.";
  }
  return null;
}

export interface RouteCheck {
  /** The address the input normalises to. Use this, not the raw input. */
  route: string;
  /** The reason it cannot be used, verbatim for the field. `null` when it can. */
  error: string | null;
}

/**
 * The one route rule, for adding a page and for re-routing one.
 *
 * `pageId` is the page being re-routed, so its own current address does not read as a clash with
 * itself. Omit it when adding.
 */
export function checkRoute(input: string, pages: PageDoc[], pageId?: string): RouteCheck {
  const route = normaliseRoute(input);

  if (input.trim() === "") {
    return { route, error: "Give the page an address, like /about." };
  }
  if (route === HOME_ROUTE && input.trim() !== HOME_ROUTE) {
    return {
      route,
      error: "Use lowercase letters, numbers and hyphens — “/about-us”.",
    };
  }
  if (route.length > MAX_ROUTE_LENGTH) {
    return {
      route,
      error: `That address is too long. Keep it under ${MAX_ROUTE_LENGTH} characters.`,
    };
  }
  if (RESERVED_ROUTES.includes(route)) {
    return {
      route,
      error: `“${route}” belongs to the console, so a page can't use it. Try “${route}-centre”.`,
    };
  }
  if (pages.some((page) => page.route === route && page.id !== pageId)) {
    return {
      route,
      error: `Another page already uses “${route}”. Every page needs its own address.`,
    };
  }
  return { route, error: null };
}

/** Whether `delete-page` and `set-page-route` will do anything for this page. */
export function canRemovePage(pages: PageDoc[], pageId: string): boolean {
  const page = pages.find((candidate) => candidate.id === pageId);
  if (!page) return false;
  return !isStructuralRoute(page.route);
}
