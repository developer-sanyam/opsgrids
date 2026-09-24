/**
 * Where a silent refresh and a logout are actually sent (ADR-0003 addendum: SSO across the
 * app hosts without a domain-wide cookie).
 *
 * The refresh cookie is host-only on `app.opsgrids.com` — never a wildcard `.opsgrids.com`
 * cookie, because the same registrable domain also carries every merchant's storefront
 * (`*.stores.opsgrids.com`), and nothing there is meant to hold a platform session. So every
 * product SPA except the dashboard and the storefront buyer bundle calls
 * `https://app.opsgrids.com/api/v1/auth/refresh` (and `/logout`) directly, cross-origin, to
 * reach the host the cookie actually lives on; the request still carries it, because
 * `app.opsgrids.com` and, say, `people.opsgrids.com` are cross-*origin* but same-*site* —
 * `SameSite=Lax` does not withhold it.
 *
 * `null` (the default) means same-origin: today's behaviour, and correct in every environment
 * for the dashboard itself, since the cookie already lives on its own host. Local dev leaves
 * this unset too — every app there talks to the gateway same-origin through its own Vite
 * proxy — the same as production behaves for the dashboard.
 */

let authOrigin: string | null = null;

/**
 * Call once, from the entry module, before the first render — the same moment each app calls
 * `configureSession`. An absolute origin, no trailing slash (`https://app.opsgrids.com`), or
 * `null` to go back to same-origin.
 */
export function configureAuthOrigin(origin: string | null): void {
  authOrigin = origin ? origin.replace(/\/+$/, "") : null;
}

/** The configured auth origin, or `null` for same-origin. */
export function getAuthOrigin(): string | null {
  return authOrigin;
}
