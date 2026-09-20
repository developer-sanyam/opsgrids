/**
 * The address rules, with no document and no DOM.
 *
 * A store's routes are enforced nowhere else — `pageForRoute` is an exact string match and the
 * live splat hands any path straight through — so "two pages at /about" is a store that is quietly
 * broken rather than a store that errors. That makes this the unit test that matters most in the
 * unit: everything downstream trusts it.
 */

import { describe, expect, it } from "vitest";

import type { PageDoc } from "@/theme/schema";

import {
  canRemovePage,
  checkRoute,
  isStructuralRoute,
  MAX_ROUTE_LENGTH,
  nameFromRoute,
  normalisePageName,
  normaliseRoute,
  RESERVED_ROUTES,
  structuralReason,
} from "./pageRoutes";

function page(id: string, route: string, name = id): PageDoc {
  return { id, route, name, sections: [] };
}

const PAGES = [
  page("home", "/", "Home"),
  page("catalogue", "/catalogue", "Catalogue"),
  page("product", "/product", "Line detail"),
  page("about", "/about", "About"),
];

describe("normalising an address", () => {
  it("takes what a merchant types and makes it a URL", () => {
    expect(normaliseRoute("About Us")).toBe("/about-us");
    expect(normaliseRoute("  Terms & Conditions  ")).toBe("/terms-conditions");
    expect(normaliseRoute("/About_Us/")).toBe("/about-us");
    expect(normaliseRoute("returns policy")).toBe("/returns-policy");
  });

  it("folds accents rather than eating the letters under them", () => {
    expect(normaliseRoute("Café")).toBe("/cafe");
  });

  it("collapses the slashes and hyphens a copy-paste leaves behind", () => {
    expect(normaliseRoute("//company///careers//")).toBe("/company/careers");
    expect(normaliseRoute("/about---us")).toBe("/about-us");
    expect(normaliseRoute("/-about-")).toBe("/about");
  });

  it("keeps a nested address, because a 120-page sitemap has groups in it", () => {
    expect(normaliseRoute("/Company/Investor Relations")).toBe("/company/investor-relations");
  });

  it("gives back the home route for anything with nothing usable in it", () => {
    expect(normaliseRoute("")).toBe("/");
    expect(normaliseRoute("   ")).toBe("/");
    expect(normaliseRoute("???")).toBe("/");
  });
});

describe("a page's name", () => {
  it("collapses whitespace and caps the length", () => {
    expect(normalisePageName("  About   us  ")).toBe("About us");
    expect(normalisePageName("x".repeat(200))).toHaveLength(60);
  });

  it("falls out of the address when the merchant gave none", () => {
    expect(nameFromRoute("/about-us")).toBe("About us");
    expect(nameFromRoute("/company/careers")).toBe("Careers");
    expect(nameFromRoute("/")).toBe("Home");
  });
});

describe("checking an address against the store", () => {
  it("accepts a new one and hands back the normalised form, not the typed one", () => {
    expect(checkRoute("Returns Policy", PAGES)).toEqual({ route: "/returns-policy", error: null });
  });

  it("refuses an empty one with something to type instead", () => {
    expect(checkRoute("   ", PAGES).error).toBe("Give the page an address, like /about.");
  });

  it("refuses input that normalises to nothing, rather than silently making it the home page", () => {
    const check = checkRoute("???", PAGES);
    expect(check.error).toBe("Use lowercase letters, numbers and hyphens — “/about-us”.");
  });

  it("refuses a duplicate — two pages at /about is a store that is broken and looks fine", () => {
    expect(checkRoute("/about", PAGES).error).toContain("Another page already uses “/about”");
    // Case and spacing do not buy a second one either.
    expect(checkRoute("  About  ", PAGES).error).toContain("Another page already uses “/about”");
  });

  it("does not call a page's own address a duplicate of itself", () => {
    expect(checkRoute("/about", PAGES, "about").error).toBeNull();
  });

  it.each(RESERVED_ROUTES)("refuses %s, which belongs to the console", (route) => {
    expect(checkRoute(route, PAGES).error).toContain("belongs to the console");
  });

  it("refuses an address longer than a URL anyone can say", () => {
    const long = `/${"a".repeat(MAX_ROUTE_LENGTH + 10)}`;
    expect(checkRoute(long, PAGES).error).toContain("too long");
  });
});

/**
 * `/`, `/catalogue` and `/product` are link targets other code resolves *implicitly*:
 * `CategoryTiles` writes `/store/catalogue?category=…` with no check that the page exists, and the
 * document has no field naming the page a product line opens on. Deleting or re-routing one breaks
 * links on a page the merchant was not looking at, so all three are locked — and `nav: "none"`,
 * which this unit adds, is the answer to "get it out of my navigation".
 */
describe("the three pages a merchant cannot remove", () => {
  it.each(["/", "/catalogue", "/product"])("locks %s", (route) => {
    expect(isStructuralRoute(route)).toBe(true);
    expect(structuralReason(route)).not.toBeNull();
  });

  it("leaves every other address alone", () => {
    expect(isStructuralRoute("/about")).toBe(false);
    expect(structuralReason("/about")).toBeNull();
    expect(canRemovePage(PAGES, "about")).toBe(true);
  });

  it("says why, in the merchant's words, so the disabled field can explain itself", () => {
    expect(structuralReason("/")).toContain("home page");
    expect(structuralReason("/catalogue")).toContain("Category tiles link to /catalogue");
    expect(structuralReason("/product")).toContain("Not in the navigation");
  });

  it.each(["home", "catalogue", "product"])("refuses to remove the %s page", (id) => {
    expect(canRemovePage(PAGES, id)).toBe(false);
  });
});
