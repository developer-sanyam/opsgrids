import { describe, expect, it } from "vitest";

import type { LocationKind } from "@/api/types";
import { LOCATION_KIND_COLOR, resolveCssColor } from "./locationKindColor";

describe("LOCATION_KIND_COLOR", () => {
  it("has an entry for every location kind, each with a swatch class and a css variable", () => {
    const kinds: LocationKind[] = [
      "zone",
      "aisle",
      "rack",
      "shelf",
      "bin",
      "staging",
      "pack_station",
      "dock",
      "receiving",
      "returns",
      "quarantine",
    ];
    for (const kind of kinds) {
      const entry = LOCATION_KIND_COLOR[kind];
      expect(entry.swatchClass).toMatch(/^bg-/);
      expect(entry.cssVar).toMatch(/^--color-/);
    }
  });
});

describe("resolveCssColor", () => {
  it("reads the variable's resolved value from the given element", () => {
    const el = document.createElement("div");
    el.style.setProperty("--color-accent", "#4f46e5");
    document.body.appendChild(el);
    expect(resolveCssColor("--color-accent", el)).toBe("#4f46e5");
    el.remove();
  });

  it("falls back to the disabled-ink token when the requested variable is not set", () => {
    const el = document.createElement("div");
    el.style.setProperty("--color-disabled", "#9aa1b0");
    document.body.appendChild(el);
    expect(resolveCssColor("--color-does-not-exist", el)).toBe("#9aa1b0");
    el.remove();
  });
});
