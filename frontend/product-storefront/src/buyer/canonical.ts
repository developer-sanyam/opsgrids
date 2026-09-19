/**
 * `<link rel="canonical">` for the buyer surface.
 *
 * A store answers on two addresses that serve identical content — the merchant's own domain and
 * `<slug>.stores.opsgrids.com` — and the platform host is never retired, because it is in every
 * QR code and printed catalogue already, and it is the fallback when a custom domain breaks
 * (ADR-0012 §8). Two hosts serving one shop split its search ranking, so the bundle names the
 * canonical one.
 *
 * **Slice 1 does not redirect.** `is_canonical: false` is not an error and not a signal to
 * navigate; a 308 needs a host map at the edge and is slice 3. A `<link>` is a weaker signal than
 * a redirect and shipping the weaker one is a recorded cost, not an oversight.
 *
 * **The host is never constructed here.** `canonical_host` is computed on the server from the
 * live domain state — it points at the platform host while a custom domain is `failing`, so the
 * pointer never aims at a name that does not answer — and rebuilding it in the browser is how the
 * two get to disagree.
 */

import { useEffect } from "react";

/**
 * The absolute canonical URL for the current page, or `null` when the server sent something that
 * is not a bare hostname.
 *
 * Returning `null` rather than guessing is deliberate: a malformed `canonical_host` must produce
 * *no* canonical link, never one pointing somewhere we did not mean. The only thing added to what
 * the server sent is the scheme — the edge serves HTTPS only — and the path the buyer is on.
 */
export function canonicalHref(canonicalHost: string, pathname: string): string | null {
  try {
    const origin = new URL(`https://${canonicalHost}`);
    /* `new URL` happily parses a value carrying a port, a path, credentials or a query. Comparing
       the parsed hostname back against the input is what refuses all of them in one check. */
    if (origin.hostname !== canonicalHost.toLowerCase()) return null;
    return new URL(pathname, origin).toString();
  } catch {
    return null;
  }
}

/**
 * Keep one `<link rel="canonical">` in the document head while a shop is on screen.
 *
 * Owned rather than declared in `buyer.html`, because the value arrives from the bootstrap call
 * and the bundle is identical for every merchant — there is nothing to put in the static head.
 * Removed on unmount so the not-found and could-not-load states never carry a stale one.
 */
export function useCanonicalLink(href: string | null): void {
  useEffect(() => {
    if (href === null) return;
    const link = document.createElement("link");
    link.rel = "canonical";
    link.href = href;
    document.head.appendChild(link);
    return () => {
      link.remove();
    };
  }, [href]);
}

/**
 * The tab's text: the shop's name, and nothing else.
 *
 * `document.title` takes a string and encodes nothing — assigning it is the safe half of the
 * escaping rule for merchant-authored text, unlike `innerHTML` on a `<title>` node, which is not.
 */
export function useDocumentTitle(title: string | null): void {
  useEffect(() => {
    if (title === null) return;
    const previous = document.title;
    document.title = title;
    return () => {
      document.title = previous;
    };
  }, [title]);
}
