/**
 * Where an in-store link points.
 *
 * The same store document is served from two addresses: `/store`, framed inside the merchant's
 * console, and `/live`, the buyer's own site with no console around it. The base is therefore a
 * parameter and not a constant — hard-coding `/store` sent every buyer on `/live` back into the
 * console the first time they tapped a nav item, and left `NavLink` comparing a `/store` href
 * against a `/live` location, so no nav item ever showed as current there.
 */

/** The console's framed preview, and the default for anything that does not say otherwise. */
export const DEFAULT_STORE_BASE = "/store";

/** A store page's route, as a URL inside this app. `"/"` is the store home. */
export function storeHref(route: string, base: string = DEFAULT_STORE_BASE): string {
  return route === "/" ? base : `${base}${route}`;
}
