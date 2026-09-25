import { describe, expect, it } from "vitest";

import { match } from "./api";
import type { MockRoute } from "./api";
import { requestedFamilies, stylesheetFor } from "./fonts";

const CONTRACT = { spec: "deal", path: "/api/v1/deals" };

describe("match", () => {
  const routes: MockRoute[] = [
    { method: "GET", path: "/api/v1/deals", fixture: "all.json", contract: CONTRACT },
    { method: "GET", path: "/api/v1/deals", query: { size: "25" }, fixture: "page.json", contract: CONTRACT },
  ];

  it("prefers the route whose query matches", () => {
    expect(match(routes, "GET", new URL("http://x/api/v1/deals?page=1&size=25"))?.fixture).toBe("page.json");
  });

  it("falls back to the path-only route", () => {
    expect(match(routes, "GET", new URL("http://x/api/v1/deals?size=200"))?.fixture).toBe("all.json");
  });

  it("matches the method and the whole path", () => {
    expect(match(routes, "POST", new URL("http://x/api/v1/deals"))).toBeUndefined();
    expect(match(routes, "GET", new URL("http://x/api/v1/deals/summary"))).toBeUndefined();
  });
});

describe("fonts", () => {
  const url = new URL(
    "https://fonts.googleapis.com/css2?family=Manrope:wght@400;500;600;700;800&family=IBM+Plex+Mono:wght@400;500&display=swap",
  );

  it("reads the families and weights a page asked for", () => {
    expect(requestedFamilies(url)).toEqual([
      { family: "Manrope", weights: [400, 500, 600, 700, 800] },
      { family: "IBM Plex Mono", weights: [400, 500] },
    ]);
  });

  it("answers with one face per weight and subset, pointing at vendored files", () => {
    const css = stylesheetFor(url);
    expect(css).toContain("font-family: 'Manrope';");
    expect(css).toContain("font-weight: 800;");
    expect(css).toContain("@fontsource-variable/manrope/files/manrope-latin-wght-normal.woff2");
    expect(css).toContain("@fontsource/ibm-plex-mono/files/ibm-plex-mono-latin-500-normal.woff2");
    expect(css).not.toContain("font-weight: 300;");
  });

  it("refuses a family nobody has vendored", () => {
    expect(() => stylesheetFor(new URL("https://fonts.googleapis.com/css2?family=Spline+Sans+Mono"))).toThrow(
      /has not vendored/,
    );
  });
});
