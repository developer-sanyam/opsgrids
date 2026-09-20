/**
 * The address space of the live store, and the one translation it needs.
 *
 * `/live` is the buyer's entrance: the same store document the console frames at `/store`, with
 * none of the console around it. Everything here is a pure string function so the route component,
 * the link interceptor and a test all answer the same question the same way.
 *
 * **Why the translation exists.** `theme/render/href.ts` builds every in-store link against the
 * console's `/store` base, and the renderer is owned elsewhere this cycle. Rather than fork the
 * renderer, the live route catches those clicks and sends them to the matching `/live` address —
 * see `LiveStore.tsx`. When `StoreRenderer` grows a base-path prop this module keeps its shape and
 * the interceptor goes away.
 */

/** The buyer-facing base. Every store page hangs off it. */
export const LIVE_BASE = "/live";

/** The console's framed-preview base, which the renderer's links still point at. */
export const CONSOLE_STORE_BASE = "/store";

/** Drop a trailing slash from anything but the root. */
function trimTrailingSlash(pathname: string): string {
  return pathname.length > 1 && pathname.endsWith("/") ? pathname.slice(0, -1) : pathname;
}

/**
 * The `PageDoc.route` a live URL is asking for. `/live` is the store's home.
 *
 * Anything deeper is passed through as a route rather than matched against a fixed list, so a
 * theme that ships `/about` gets a working address without a change here. A store with no page at
 * that route is the renderer's own empty state, not a 404 from this app.
 */
export function storeRouteForPath(pathname: string): string {
  const path = trimTrailingSlash(pathname);
  if (path === LIVE_BASE) return "/";
  if (!path.startsWith(`${LIVE_BASE}/`)) return "/";
  return path.slice(LIVE_BASE.length);
}

/** The live URL for a `PageDoc.route`. */
export function livePath(storeRoute: string): string {
  return storeRoute === "/" ? LIVE_BASE : `${LIVE_BASE}${storeRoute}`;
}

/**
 * The live address matching a console `/store` URL, or `null` when the URL is not one.
 *
 * `null` means "leave this link alone": an address outside the store — the editor, the gallery,
 * `mailto:` — must keep working exactly as written.
 */
export function liveTargetForConsolePath(pathname: string): string | null {
  const path = trimTrailingSlash(pathname);
  if (path === CONSOLE_STORE_BASE) return LIVE_BASE;
  if (!path.startsWith(`${CONSOLE_STORE_BASE}/`)) return null;
  return `${LIVE_BASE}${path.slice(CONSOLE_STORE_BASE.length)}`;
}

/**
 * What the browser tab says.
 *
 * A live site's tab carries the shop's name, and the page it is on first — that is the half a
 * buyer can still read with fifteen tabs open.
 */
export function documentTitle(brandName: string, storeRoute: string, pageName: string | null): string {
  if (storeRoute === "/") return brandName;
  if (pageName === null) return `Page not found · ${brandName}`;
  return `${pageName} · ${brandName}`;
}

/** XML-escape text bound for the favicon's `<text>` node. A brand may legitimately contain `&`. */
function escapeXml(value: string): string {
  return value
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&apos;");
}

/**
 * The store's mark as a favicon, drawn from the same three pieces of merchant data the store's
 * own header uses: the logo text, the accent, and the accent's text colour.
 *
 * Generated rather than fetched — there is no asset service, and a live-looking tab is the whole
 * point of this route. The numbers below are the icon's internal geometry on a 64-unit canvas, not
 * app styling; nothing in the document is sized from them.
 */
export function faviconDataUri(logoText: string, background: string, color: string): string {
  const initials = escapeXml(logoText.slice(0, 2).toUpperCase());
  const svg =
    `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">` +
    `<rect width="64" height="64" rx="14" fill="${escapeXml(background)}"/>` +
    `<text x="32" y="44" text-anchor="middle" font-family="system-ui, sans-serif" ` +
    `font-size="32" font-weight="700" fill="${escapeXml(color)}">${initials}</text>` +
    `</svg>`;
  return `data:image/svg+xml,${encodeURIComponent(svg)}`;
}
