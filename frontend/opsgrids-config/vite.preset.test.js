import { describe, expect, it } from "vitest";

import { assetsInlineLimit } from "./vite.preset.js";

describe("assetsInlineLimit", () => {
  it("never inlines a font, however small, because the edge CSP forbids data: fonts", () => {
    for (const file of ["/a/manrope-latin-ext-wght-normal.woff2", "x.WOFF", "y.ttf", "z.otf"]) {
      expect(assetsInlineLimit(file)).toBe(false);
    }
  });

  it("leaves every other asset to Vite's own size rule", () => {
    expect(assetsInlineLimit("/a/logo.svg")).toBeUndefined();
    expect(assetsInlineLimit("/a/photo.png")).toBeUndefined();
  });
});
