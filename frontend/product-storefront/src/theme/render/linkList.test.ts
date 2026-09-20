/**
 * The one-per-line link convention. Pure parsing, so it is tested without a DOM.
 *
 * The tolerance is the feature: a merchant types into a plain textarea, and every shape they can
 * plausibly leave behind has to come out as something renderable rather than as a dead link.
 */

import { beforeEach, describe, expect, it, vi } from "vitest";

import { classifyHref, isExternalHref, isSafeHref, parseLinkList } from "./linkList";

/* A refused destination is named in the dev console on purpose. It is silenced here so the suite's
   output stays readable; one test below asserts the warning itself. `restoreMocks` puts the real
   `console.warn` back after every test. */
beforeEach(() => {
  vi.spyOn(console, "warn").mockImplementation(() => {});
});

describe("parsing a link column", () => {
  it("reads Label | /href", () => {
    expect(parseLinkList("Seating | /store/catalogue")).toEqual([
      { label: "Seating", href: "/store/catalogue" },
    ]);
  });

  it("treats a line with no pipe as label-only text, not a link", () => {
    expect(parseLinkList("Mon to Sat, 10am to 7pm")).toEqual([
      { label: "Mon to Sat, 10am to 7pm", href: null },
    ]);
  });

  it("treats a pipe with nothing after it as label-only text too", () => {
    expect(parseLinkList("Coming soon |")).toEqual([{ label: "Coming soon", href: null }]);
  });

  it("splits on the first pipe only, and what follows is judged as a destination", () => {
    // The split is still on the first pipe — the label is "Delivery" and not the whole line. What
    // follows it is not a destination the href policy permits, so the entry becomes label-only
    // text rather than a link to "assembly | /store/contact" that could never have resolved.
    expect(parseLinkList("Delivery | assembly | /store/contact")).toEqual([
      { label: "Delivery", href: null },
    ]);
  });

  it("drops blank lines and a line with no label", () => {
    expect(parseLinkList("Shop | /store\n\n   \n| /orphan\nHelp | /help")).toEqual([
      { label: "Shop", href: "/store" },
      { label: "Help", href: "/help" },
    ]);
  });

  it("trims the whitespace a merchant leaves around the pipe", () => {
    expect(parseLinkList("  Returns   |   /store/contact  ")).toEqual([
      { label: "Returns", href: "/store/contact" },
    ]);
  });

  it("is empty for an empty field", () => {
    expect(parseLinkList("")).toEqual([]);
  });
});

describe("telling an internal path from one that leaves the app", () => {
  it("routes an internal path", () => {
    expect(isExternalHref("/store/catalogue")).toBe(false);
    expect(isExternalHref("/store/catalogue?category=beds")).toBe(false);
  });

  it("does not route a URL, a phone number or an email", () => {
    expect(isExternalHref("https://instagram.com")).toBe(true);
    expect(isExternalHref("http://supplier.example.in/rates")).toBe(true);
    expect(isExternalHref("tel:+912912440000")).toBe(true);
    expect(isExternalHref("mailto:hello@example.in")).toBe(true);
  });

  // "Not external" is not "safe": a refused destination is neither, and a caller reading this as
  // a two-way switch would hand `javascript:` to the router. Callers use `classifyHref`.
  it("calls a refused destination neither external nor safe", () => {
    expect(isExternalHref("javascript:alert(1)")).toBe(false);
    expect(isSafeHref("javascript:alert(1)")).toBe(false);
    expect(isExternalHref("//cdn.example.com/x")).toBe(false);
    expect(isSafeHref("//cdn.example.com/x")).toBe(false);
  });
});

/* ------------------------------ the allow-list ----------------------------- */

describe("what a merchant may point a link at", () => {
  it("permits the four schemes a storefront link legitimately uses", () => {
    expect(classifyHref("https://instagram.com")).toEqual({
      kind: "external",
      href: "https://instagram.com",
    });
    expect(classifyHref("http://supplier.example.in/rates")).toEqual({
      kind: "external",
      href: "http://supplier.example.in/rates",
    });
    expect(classifyHref("mailto:hello@example.in")).toEqual({
      kind: "external",
      href: "mailto:hello@example.in",
    });
    expect(classifyHref("tel:+912912440000")).toEqual({
      kind: "external",
      href: "tel:+912912440000",
    });
  });

  it("permits a root-relative path, query and all", () => {
    expect(classifyHref("/store/catalogue?category=beds#top")).toEqual({
      kind: "internal",
      href: "/store/catalogue?category=beds#top",
    });
  });

  it("hands back the address as typed rather than a re-serialised one", () => {
    // `new URL().href` would add a trailing slash the merchant did not write, and the store
    // document is their text — the policy judges it, it does not edit it.
    expect(classifyHref("https://instagram.com")).toEqual({
      kind: "external",
      href: "https://instagram.com",
    });
  });

  it("trims the whitespace a paste leaves around an address", () => {
    expect(classifyHref("  https://instagram.com  ")).toEqual({
      kind: "external",
      href: "https://instagram.com",
    });
    expect(classifyHref("  /store/catalogue ")).toEqual({
      kind: "internal",
      href: "/store/catalogue",
    });
  });

  it("refuses the executable schemes", () => {
    for (const value of [
      "javascript:alert(1)",
      "javascript:void(0)",
      "vbscript:msgbox(1)",
      "data:text/html;base64,PHNjcmlwdD5hbGVydCgxKTwvc2NyaXB0Pg==",
      "file:///etc/passwd",
      "blob:https://example.in/9a",
      "ftp://files.example.in/rates.pdf",
      "intent://scan#Intent;scheme=zxing;end",
    ]) {
      expect(classifyHref(value), value).toEqual({ kind: "refused" });
    }
  });

  it("refuses a scheme however it is cased", () => {
    for (const value of ["JAVASCRIPT:alert(1)", "JaVaScRiPt:alert(1)", "Data:text/html,x"]) {
      expect(classifyHref(value), value).toEqual({ kind: "refused" });
    }
  });

  it("refuses a scheme with whitespace or a control character inside it", () => {
    // A browser strips these before it dispatches the URL, so each one is a live `javascript:`
    // link that a `startsWith` check and a scheme pattern both wave through.
    for (const value of [
      " javascript:alert(1)",
      "\tjavascript:alert(1)",
      "java\tscript:alert(1)",
      "java\nscript:alert(1)",
      "java\rscript:alert(1)",
      "java\u0000script:alert(1)",
      "jav\tascript:alert(1)",
    ]) {
      expect(classifyHref(value), JSON.stringify(value)).toEqual({ kind: "refused" });
    }
  });

  it("refuses a protocol-relative address, backslash included", () => {
    expect(classifyHref("//cdn.example.com/x")).toEqual({ kind: "refused" });
    expect(classifyHref("\\\\cdn.example.com/x")).toEqual({ kind: "refused" });
    // A browser reads a backslash in the authority position as a slash, so `/\host` leaves the
    // site exactly as `//host` does while looking like an ordinary path.
    expect(classifyHref("/\\cdn.example.com/x")).toEqual({ kind: "refused" });
  });

  it("refuses a bare fragment, a relative path and an empty field", () => {
    expect(classifyHref("#")).toEqual({ kind: "refused" });
    expect(classifyHref("#section-2")).toEqual({ kind: "refused" });
    expect(classifyHref("catalogue")).toEqual({ kind: "refused" });
    expect(classifyHref("../admin")).toEqual({ kind: "refused" });
    expect(classifyHref("")).toEqual({ kind: "refused" });
    expect(classifyHref("   ")).toEqual({ kind: "refused" });
  });

  it("refuses a permitted scheme with nothing after it", () => {
    // These parse, and they carry a scheme on the list, and they still go nowhere.
    expect(classifyHref("mailto:")).toEqual({ kind: "refused" });
    expect(classifyHref("tel:")).toEqual({ kind: "refused" });
  });

  it("refuses text that is not an address at all", () => {
    expect(classifyHref("Mon to Sat, 10am to 7pm")).toEqual({ kind: "refused" });
    expect(classifyHref("https://")).toEqual({ kind: "refused" });
    expect(classifyHref("::::")).toEqual({ kind: "refused" });
  });

  it("names the refused value once in the console, so it is not a silent disappearance", () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});

    classifyHref("javascript:oneOffForThisTest(1)");
    classifyHref("javascript:oneOffForThisTest(1)");

    expect(warn).toHaveBeenCalledTimes(1);
    expect(String(warn.mock.calls[0]?.[0])).toContain("javascript:oneOffForThisTest(1)");
  });
});

describe("a link column carries the same policy", () => {
  it("drops a refused destination to label-only text", () => {
    expect(parseLinkList("Offers | javascript:alert(1)")).toEqual([
      { label: "Offers", href: null },
    ]);
    expect(parseLinkList("Brochure | data:text/html,x")).toEqual([
      { label: "Brochure", href: null },
    ]);
  });

  it("keeps the destinations a contact column is made of", () => {
    expect(
      parseLinkList(
        "+91 291 244 0000 | tel:+912912440000\nhello@example.in | mailto:hello@example.in\nSeating | /store/catalogue",
      ),
    ).toEqual([
      { label: "+91 291 244 0000", href: "tel:+912912440000" },
      { label: "hello@example.in", href: "mailto:hello@example.in" },
      { label: "Seating", href: "/store/catalogue" },
    ]);
  });
});
