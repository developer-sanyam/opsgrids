/**
 * "Fork on adopt" is the whole model, so it is the thing most worth pinning: a store must be a
 * deep copy of its theme, and mutating the store must be invisible to the theme.
 */

import { describe, expect, it } from "vitest";

import { adoptTheme, clonePages, pageForRoute } from "./store";
import { THEMES, foundry, suggestedBrand } from "./themes";

const BRAND = { name: "Test Supply", tagline: "Testing", logoText: "TS" };

describe("adoptTheme", () => {
  it("copies the theme's tokens and pages into a store document", () => {
    const store = adoptTheme(foundry, BRAND);

    expect(store.themeKey).toBe("foundry");
    expect(store.brand).toEqual(BRAND);
    expect(store.tokens).toEqual(foundry.tokens);
    expect(store.pages.map((p) => p.route)).toEqual(foundry.pages.map((p) => p.route));
  });

  it("deep-copies rather than aliasing — mutating the store cannot reach the theme", () => {
    const store = adoptTheme(foundry, BRAND);

    const themeHome = foundry.pages[0];
    const storeHome = store.pages[0];
    if (!themeHome || !storeHome) throw new Error("The foundry theme must ship a first page");

    const themeSection = themeHome.sections[0];
    const storeSection = storeHome.sections[0];
    if (!themeSection || !storeSection) throw new Error("The first page must ship a first section");

    // Every level: the array, the instance, the settings bag and the layout box.
    expect(store.pages).not.toBe(foundry.pages);
    expect(storeHome).not.toBe(themeHome);
    expect(storeHome.sections).not.toBe(themeHome.sections);
    expect(storeSection).not.toBe(themeSection);
    expect(storeSection.settings).not.toBe(themeSection.settings);
    expect(storeSection.layout).not.toBe(themeSection.layout);
    expect(store.tokens).not.toBe(foundry.tokens);
    expect(store.brand).not.toBe(BRAND);

    const originalHeading = themeSection.settings.heading;
    const originalX = themeSection.layout.x;
    const originalSectionCount = themeHome.sections.length;
    const originalAccent = foundry.tokens.colorAccent;

    storeSection.settings.heading = "Edited by the merchant";
    storeSection.layout.x = 6;
    storeHome.sections.push({
      id: "new-block",
      type: "rich-text",
      settings: {},
      layout: { x: 0, y: 99, w: 12, h: 4 },
    });
    store.tokens.colorAccent = "#000000";
    store.brand.name = "Renamed";

    expect(themeSection.settings.heading).toBe(originalHeading);
    expect(themeSection.layout.x).toBe(originalX);
    expect(themeHome.sections).toHaveLength(originalSectionCount);
    expect(foundry.tokens.colorAccent).toBe(originalAccent);
    expect(BRAND.name).toBe("Test Supply");
  });

  it("keeps the brand when a different theme is adopted, and replaces the pages", () => {
    const first = adoptTheme(foundry, BRAND);
    const other = THEMES.find((t) => t.key !== foundry.key);
    if (!other) throw new Error("The library must ship more than one theme");

    const second = adoptTheme(other, first.brand);

    expect(second.brand).toEqual(BRAND);
    expect(second.themeKey).toBe(other.key);
    expect(second.pages.map((p) => p.id)).toEqual(other.pages.map((p) => p.id));
  });
});

describe("clonePages", () => {
  it("produces pages that are equal but not identical", () => {
    const copy = clonePages(foundry.pages);
    expect(copy).toEqual(foundry.pages);
    expect(copy[0]).not.toBe(foundry.pages[0]);
  });
});

describe("pageForRoute", () => {
  it("finds a page by route and returns null for one that does not exist", () => {
    const store = adoptTheme(foundry, suggestedBrand("foundry"));
    expect(pageForRoute(store, "/")?.route).toBe("/");
    expect(pageForRoute(store, "/nowhere")).toBeNull();
  });
});
