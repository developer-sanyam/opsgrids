/**
 * The platform app hosts a post-login `next` redirect may land on (ADR-0003 addendum) — the
 * dashboard's own open-redirect allow-list, and the one place this list is written down for
 * every SPA to share.
 *
 * Every named `https://` site in `infrastructure/edge/Caddyfile` except the anonymous
 * storefront surfaces: `*.stores.opsgrids.com` and a merchant's custom domain hold no
 * platform session, are served by the catch-all rather than a named site, and must never be a
 * redirect target — login or otherwise. `tests/infra/test_sso_redirect_allowlist.py` pins this
 * list against the Caddyfile itself, so the two cannot drift without a failing test naming
 * which one moved.
 */
export const PLATFORM_APP_HOSTS: readonly string[] = [
  "app.opsgrids.com",
  "ops.opsgrids.com",
  "worker.opsgrids.com",
  "people.opsgrids.com",
  "revenue.opsgrids.com",
  "pulse.opsgrids.com",
  "projects.opsgrids.com",
  "support.opsgrids.com",
  "marketing.opsgrids.com",
  "finance.opsgrids.com",
  "it.opsgrids.com",
  "store.opsgrids.com",
  "engage.opsgrids.com",
];

/**
 * `true` only for a well-formed, absolute `https://` URL, carrying no port and no userinfo,
 * whose host is *exactly* one of `PLATFORM_APP_HOSTS` — never a prefix or suffix match.
 *
 * Rejects everything else, and each of these is a distinct open-redirect shape that a naive
 * check gets wrong: a host not on the list at all (including every storefront host and every
 * custom domain), `http:` (there is no such thing as a plain-HTTP platform host), `javascript:`
 * and any other exotic scheme, a protocol-relative value (`//evil.example/...`, which `new
 * URL()` refuses to parse without a base — exactly the point), and a userinfo trick
 * (`https://app.opsgrids.com@evil.example/`, whose *host* is `evil.example` — this function
 * reads `hostname`, never anything a browser's address bar would show first).
 */
export function isAllowedRedirectTarget(candidate: string): boolean {
  let url: URL;
  try {
    url = new URL(candidate);
  } catch {
    return false;
  }
  if (url.protocol !== "https:") return false;
  if (url.port !== "") return false;
  if (url.username !== "" || url.password !== "") return false;
  return PLATFORM_APP_HOSTS.includes(url.hostname);
}
