import { describe, expect, it } from "vitest";

import {
  documentTitle,
  faviconDataUri,
  livePath,
  liveTargetForConsolePath,
  storeRouteForPath,
} from "./liveRoutes";

/**
 * The live site's address arithmetic, tested where it is pure. The component test below exercises
 * the same functions through the DOM; these pin the edges a rendered click never reaches.
 */

describe("storeRouteForPath", () => {
  it("reads the store page out of a live address", () => {
    expect(storeRouteForPath("/live")).toBe("/");
    expect(storeRouteForPath("/live/")).toBe("/");
    expect(storeRouteForPath("/live/catalogue")).toBe("/catalogue");
    expect(storeRouteForPath("/live/product")).toBe("/product");
  });

  it("passes through a route no theme ships today, rather than guessing", () => {
    expect(storeRouteForPath("/live/about")).toBe("/about");
  });

  it("round-trips with livePath", () => {
    for (const route of ["/", "/catalogue", "/product", "/about"]) {
      expect(storeRouteForPath(livePath(route))).toBe(route);
    }
  });
});

describe("liveTargetForConsolePath", () => {
  it("maps the console's store addresses onto the live ones", () => {
    expect(liveTargetForConsolePath("/store")).toBe("/live");
    expect(liveTargetForConsolePath("/store/")).toBe("/live");
    expect(liveTargetForConsolePath("/store/catalogue")).toBe("/live/catalogue");
  });

  it("leaves every other address alone", () => {
    // `null` is what keeps the editor, the gallery and a lookalike prefix working as written.
    expect(liveTargetForConsolePath("/")).toBeNull();
    expect(liveTargetForConsolePath("/editor")).toBeNull();
    expect(liveTargetForConsolePath("/storefront-settings")).toBeNull();
    expect(liveTargetForConsolePath("/live/catalogue")).toBeNull();
  });
});

describe("documentTitle", () => {
  it("puts the shop's name on the home tab and the page in front of it elsewhere", () => {
    expect(documentTitle("Verma Traders", "/", "Home")).toBe("Verma Traders");
    expect(documentTitle("Verma Traders", "/catalogue", "Catalogue")).toBe(
      "Catalogue · Verma Traders",
    );
  });

  it("says so when the store has no page at that address", () => {
    expect(documentTitle("Verma Traders", "/about", null)).toBe("Page not found · Verma Traders");
  });
});

describe("faviconDataUri", () => {
  it("draws the store's initials in the store's accent", () => {
    const uri = faviconDataUri("VT", "#0B7A24", "#ffffff");
    expect(uri.startsWith("data:image/svg+xml,")).toBe(true);
    const svg = decodeURIComponent(uri.slice("data:image/svg+xml,".length));
    expect(svg).toContain("#0B7A24");
    expect(svg).toContain(">VT</text>");
  });

  it("escapes a brand that would otherwise break the SVG", () => {
    const svg = decodeURIComponent(
      faviconDataUri("B&", "#000000", "#ffffff").slice("data:image/svg+xml,".length),
    );
    expect(svg).toContain("B&amp;");
    expect(svg).not.toContain(">B&<");
  });
});
