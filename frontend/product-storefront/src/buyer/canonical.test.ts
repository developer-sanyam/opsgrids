/**
 * `canonicalHref` — the one place a host the server sent becomes a URL.
 *
 * The rule it enforces: **render what the server sent, or render nothing.** `canonical_host` is
 * computed from the live domain state (it points back at the platform host while a custom domain
 * is `failing`, so the pointer never aims at a name that does not answer), and a browser that
 * re-derives it is a second implementation that can disagree with the first.
 */

import { describe, expect, it } from "vitest";

import { canonicalHref } from "./canonical";

describe("canonicalHref", () => {
  it("puts the server's host under https, with the page's own path", () => {
    expect(canonicalHref("merchant.com", "/")).toBe("https://merchant.com/");
    expect(canonicalHref("shop.merchant.com", "/catalogue")).toBe(
      "https://shop.merchant.com/catalogue",
    );
  });

  it("accepts a platform host, which is what most stores are canonical on today", () => {
    // ADR-0012's erratum 3: the custom-domain branch is unreachable through any API yet, so
    // `canonical_host` is the platform host in production, correctly (§8).
    expect(canonicalHref("greenleaf.stores.opsgrids.com", "/")).toBe(
      "https://greenleaf.stores.opsgrids.com/",
    );
  });

  it("accepts a punycode A-label unchanged", () => {
    // The server converts a U-label before storing it; nothing here re-encodes or decodes.
    expect(canonicalHref("xn--mnchen-shop-thb.de", "/")).toBe("https://xn--mnchen-shop-thb.de/");
  });

  it.each([
    ["a path smuggled into the host", "merchant.com/evil"],
    ["a query smuggled into the host", "merchant.com?x=1"],
    ["a port", "merchant.com:8443"],
    ["credentials", "user:pass@merchant.com"],
    ["a second scheme", "https://merchant.com"],
    ["an empty host", ""],
    ["a space", "merchant .com"],
  ])("emits nothing for %s", (_label, host) => {
    // No canonical link at all is strictly better than one pointing somewhere we did not mean.
    expect(canonicalHref(host, "/")).toBeNull();
  });

  it("case-folds a host, because a hostname is case-insensitive and the URL is not the host", () => {
    /* The server already lower-cases before it stores or returns anything, so this should never
       arrive. Case-folding it is `URL`'s own normalisation of a case-insensitive name — it names
       the same host — which is why the guard compares against `toLowerCase()` and not the raw
       string. Anything that would change *which* host is addressed is refused above. */
    expect(canonicalHref("Merchant.com", "/")).toBe("https://merchant.com/");
  });
});
