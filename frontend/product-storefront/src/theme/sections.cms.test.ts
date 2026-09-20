/**
 * The library-level invariants the content seven have to keep.
 *
 * `sections.retail.test.ts` already locks the two that are library-wide — no ninth field type
 * anywhere, and every `select` carrying its own options — and those now sweep these seven too.
 * What is left is what is specific to this pack: the slot convention, which is the thing a
 * renderer and a definition can silently disagree about, and the palette placement.
 */

import { describe, expect, it } from "vitest";

import { BLEED_SETTING } from "./schema";
import { defaultSettings, SECTION_DEFS, sectionDef } from "./sections";
import { CMS_SECTION_DEFS } from "./sections.cms";

const CMS_TYPES = [
  "page-header",
  "long-form",
  "media-gallery",
  "team-grid",
  "card-deck",
  "credential-strip",
  "video",
];

/** How many numbered slots each repeating section declares, and why that count. */
const SLOT_COUNTS: Record<string, number> = {
  // Twelve divides by 2, 3 and 4 — every column count this section offers.
  "media-gallery": 12,
  // Six is a leadership row of three or a departmental row of six.
  "team-grid": 6,
  "card-deck": 6,
  "credential-strip": 6,
  // Four films is a page; more than four is a library, which wants records and not settings.
  video: 4,
};

describe("the content seven", () => {
  it("are all merged into the library", () => {
    for (const type of CMS_TYPES) {
      expect(sectionDef(type), type).not.toBeNull();
    }
    expect(CMS_SECTION_DEFS.map((def) => def.type)).toEqual(CMS_TYPES);
  });

  it("do not collide with a type that was already declared", () => {
    const types = SECTION_DEFS.map((def) => def.type);
    expect(new Set(types).size).toBe(types.length);
  });

  it("keeps every key inside a definition unique", () => {
    for (const def of CMS_SECTION_DEFS) {
      const keys = def.fields.map((field) => field.key);
      expect(new Set(keys).size, def.type).toBe(keys.length);
    }
  });

  it("ships a default for every field, so a freshly dropped instance is complete", () => {
    for (const def of CMS_SECTION_DEFS) {
      const defaults = defaultSettings(def.type);
      for (const field of def.fields) {
        expect(Object.hasOwn(defaults, field.key), `${def.type}.${field.key}`).toBe(true);
      }
    }
  });

  it("puts each one in a group the palette already shows", () => {
    const groups = Object.fromEntries(CMS_SECTION_DEFS.map((def) => [def.type, def.group]));
    expect(groups).toEqual({
      "page-header": "content",
      "long-form": "content",
      "media-gallery": "content",
      "team-grid": "content",
      "card-deck": "content",
      // Certifications are proof, which is the group the logo wall and the testimonials sit in.
      "credential-strip": "social",
      video: "content",
    });
  });

  it("declares the bleed toggle by the page layer's own constant, and only where it makes sense", () => {
    const bleeding = CMS_SECTION_DEFS.filter((def) =>
      def.fields.some((field) => field.key === BLEED_SETTING),
    ).map((def) => def.type);

    // Only the title block spans the canvas: a grid of pictures or a column of prose running to
    // the window edge is a layout nobody asked for.
    expect(bleeding).toEqual(["page-header"]);
    expect(defaultSettings("page-header")[BLEED_SETTING]).toBe(false);
  });
});

describe("the numbered-slot convention", () => {
  it.each(Object.entries(SLOT_COUNTS))(
    "%s declares exactly %i slots, every one spelled itemN",
    (type, count) => {
      const def = sectionDef(type);
      if (!def) throw new Error(`${type} should be in the library`);

      const indices = new Set<number>();
      for (const field of def.fields) {
        const match = /^item(\d+)[A-Z]/.exec(field.key);
        if (match) indices.add(Number(match[1]));
      }

      expect([...indices].sort((a, b) => a - b)).toEqual(
        Array.from({ length: count }, (_, index) => index + 1),
      );
    },
  );

  it.each(Object.keys(SLOT_COUNTS))("%s gives every slot the same fields", (type) => {
    const def = sectionDef(type);
    if (!def) throw new Error(`${type} should be in the library`);

    const shape = (slot: number) =>
      def.fields
        .map((field) => new RegExp(`^item${slot}([A-Z].*)$`).exec(field.key)?.[1])
        .filter((suffix): suffix is string => suffix !== undefined)
        .sort();

    const first = shape(1);
    expect(first.length).toBeGreaterThan(0);
    for (let slot = 2; slot <= (SLOT_COUNTS[type] ?? 0); slot += 1) {
      expect(shape(slot), `${type} slot ${slot}`).toEqual(first);
    }
  });

  it.each(Object.keys(SLOT_COUNTS))("%s ships its last slot empty, so there is room to grow", (type) => {
    const defaults = defaultSettings(type);
    const last = SLOT_COUNTS[type] ?? 0;
    for (const [key, value] of Object.entries(defaults)) {
      if (!key.startsWith(`item${last}`)) continue;
      expect(value === "" || value === null, `${type}.${key}`).toBe(true);
    }
  });
});
