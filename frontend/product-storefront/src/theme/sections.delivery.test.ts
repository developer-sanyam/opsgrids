/**
 * The library-level invariants behind the delivery check, and the retail themes that place it.
 *
 * The expensive one is the first: **a merchant must not be able to type a delivery promise into
 * this block.** That is the whole feature. Today's announcement bar has `promiseText` as free
 * text, so a store tells every buyer in the country it delivers in eleven minutes because that is
 * the string in the box; the zone map behind the check is what replaces it with the truth. A
 * `promiseMinutes` or `deliveryDate` setting arriving here later — added in good faith, to "let a
 * merchant override it" — would put the old defect straight back, and nothing else in the codebase
 * would notice.
 */

import { describe, expect, it } from "vitest";

import type { SectionInstance } from "./schema";
import { defaultSettings, sectionDef } from "./sections";
import { DELIVERY_SECTION_DEFS } from "./sections.delivery";
import { atelier } from "./themes/atelier";
import { quickcart } from "./themes/quickcart";
import { THEMES } from "./themes";

const CHECK = "delivery-check";

describe("the delivery check's settings", () => {
  it("is declared once, and the library can find it", () => {
    expect(DELIVERY_SECTION_DEFS.map((def) => def.type)).toEqual([CHECK]);
    expect(sectionDef(CHECK)?.name).toBe("Delivery check");
  });

  it("lets a merchant author no promise of any kind", () => {
    const keys = sectionDef(CHECK)?.fields.map((field) => field.key) ?? [];

    // The answer is the server's, from the zone map, through one selector (ADR-0010 §5). A setting
    // named like any of these would be a second source of truth for it.
    for (const forbidden of ["promiseMinutes", "promiseDays", "promiseDate", "promiseKind", "mode", "postalCodes"]) {
      expect(keys, `"${forbidden}" must not be a setting`).not.toContain(forbidden);
    }
  });

  it("names the one string that *is* the merchant's, and says what supersedes it", () => {
    const field = sectionDef(CHECK)?.fields.find((f) => f.key === "idlePromiseText");

    expect(field?.default).toBe("Delivery in 11 minutes");
    // §15's precedence rule, written where the merchant reads it rather than only in the ADR.
    expect(field?.help).toMatch(/replaces it/);
  });

  it("keeps every setting flat — no arrays smuggled in as a value", () => {
    for (const value of Object.values(defaultSettings(CHECK))) {
      expect(["string", "number", "boolean"].includes(typeof value) || value === null).toBe(true);
    }
  });
});

function sections(theme: { pages: { sections: SectionInstance[] }[] }): SectionInstance[] {
  return theme.pages.flatMap((page) => page.sections);
}

describe("the themes that place it", () => {
  it("is on both retail themes and on no trade theme", () => {
    for (const theme of THEMES) {
      const placed = sections(theme).some((section) => section.type === CHECK);
      // A trade store takes enquiries; it has no doorstep promise to make. The renderer guards this
      // too — this is the fixture side of the same rule, so a theme cannot ship the claim.
      expect(placed, `${theme.key} (${theme.mode})`).toBe(theme.mode === "retail");
    }
  });

  it("gives each store its own published handle, because the request names the store", () => {
    for (const section of sections(quickcart).filter((s) => s.type === CHECK)) {
      expect(section.settings.storeSlug).toBe("quickcart");
    }
    for (const section of sections(atelier).filter((s) => s.type === CHECK)) {
      expect(section.settings.storeSlug).toBe("atelier");
    }
  });

  it("leaves no section overlapping another on any page it was inserted into", () => {
    // Inserting a row-3 block meant shifting every `y` below it. A missed one overlaps, and above
    // the stacking width the grid paints the two on top of each other.
    for (const theme of [quickcart, atelier]) {
      for (const page of theme.pages) {
        const ordered = [...page.sections].sort((a, b) => a.layout.y - b.layout.y);
        for (let i = 1; i < ordered.length; i += 1) {
          const above = ordered[i - 1];
          const below = ordered[i];
          if (above === undefined || below === undefined) continue;
          expect(
            above.layout.y + above.layout.h,
            `${theme.key} ${page.route}: ${above.id} runs into ${below.id}`,
          ).toBeLessThanOrEqual(below.layout.y);
        }
      }
    }
  });
});
