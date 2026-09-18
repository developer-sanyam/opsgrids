/**
 * The library-level invariants the retail four have to keep.
 *
 * The expensive one is the field-type guard. `SettingField["type"]` has eight members, and every
 * one of them is a branch in `SettingControl.tsx` — so a ninth is a two-file change, in two
 * different halves of the product, that is easy to make by accident and impossible to notice from
 * the section side alone. The check is three lines and it locks that invariant for the whole
 * library, not only for this file.
 */

import { describe, expect, it } from "vitest";

import { BLEED_SETTING } from "./schema";
import type { SectionDef } from "./schema";
import { defaultSettings, SECTION_DEFS, sectionDef } from "./sections";
import { RETAIL_SECTION_DEFS } from "./sections.retail";

/** The eight the editor knows how to draw a control for. Adding a ninth is a decision, not a diff. */
const FIELD_TYPES = new Set([
  "text",
  "textarea",
  "number",
  "select",
  "toggle",
  "color",
  "image",
  "product-source",
]);

const RETAIL_TYPES = ["announcement-bar", "collection-row", "promo-banner", "footer-columns"];

describe("the section library's field types", () => {
  it("declares no ninth field type anywhere in the library", () => {
    for (const def of SECTION_DEFS) {
      for (const field of def.fields) {
        expect(FIELD_TYPES.has(field.type), `${def.type}.${field.key} is "${field.type}"`).toBe(true);
      }
    }
  });

  it("gives every select its own options, so the editor never falls back to free text", () => {
    for (const def of SECTION_DEFS) {
      for (const field of def.fields) {
        if (field.type !== "select") continue;
        expect(field.options?.length ?? 0, `${def.type}.${field.key}`).toBeGreaterThan(0);
      }
    }
  });
});

describe("the retail four", () => {
  it("are all merged into the library", () => {
    for (const type of RETAIL_TYPES) {
      expect(sectionDef(type), type).not.toBeNull();
    }
  });

  it("do not collide with a type that was already declared", () => {
    const types = SECTION_DEFS.map((def) => def.type);
    expect(new Set(types).size).toBe(types.length);
  });

  it("keeps every key inside a definition unique", () => {
    for (const def of RETAIL_SECTION_DEFS) {
      const keys = def.fields.map((field) => field.key);
      expect(new Set(keys).size, def.type).toBe(keys.length);
    }
  });

  it("declares the bleed toggle by the page layer's own constant, never a copy of the string", () => {
    const bleeding = RETAIL_SECTION_DEFS.filter((def: SectionDef) =>
      def.fields.some((field) => field.key === BLEED_SETTING),
    ).map((def) => def.type);

    // The two chrome sections reach the canvas edge in both references; the banner does not
    // unless the merchant says so.
    expect(bleeding).toEqual(["announcement-bar", "promo-banner", "footer-columns"]);
    expect(defaultSettings("announcement-bar")[BLEED_SETTING]).toBe(true);
    expect(defaultSettings("footer-columns")[BLEED_SETTING]).toBe(true);
    expect(defaultSettings("promo-banner")[BLEED_SETTING]).toBe(false);
  });

  it("puts each one in a group the palette already shows", () => {
    const groups = Object.fromEntries(RETAIL_SECTION_DEFS.map((def) => [def.type, def.group]));
    expect(groups).toEqual({
      "announcement-bar": "layout",
      "collection-row": "commerce",
      "promo-banner": "content",
      "footer-columns": "layout",
    });
  });

  it("ships a default for every field, so a freshly dropped instance is complete", () => {
    for (const def of RETAIL_SECTION_DEFS) {
      const defaults = defaultSettings(def.type);
      for (const field of def.fields) {
        expect(Object.hasOwn(defaults, field.key), `${def.type}.${field.key}`).toBe(true);
      }
    }
  });
});
