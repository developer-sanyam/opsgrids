/**
 * Turning a theme into a store, and keeping the two apart afterwards.
 */

import type { PageDoc, SectionInstance, StoreDoc, Theme, ThemeTokens } from "./schema";

export function cloneTokens(tokens: ThemeTokens): ThemeTokens {
  return { ...tokens };
}

function cloneSection(section: SectionInstance): SectionInstance {
  return {
    id: section.id,
    type: section.type,
    settings: { ...section.settings },
    layout: { ...section.layout },
  };
}

export function clonePage(page: PageDoc): PageDoc {
  return {
    id: page.id,
    route: page.route,
    name: page.name,
    sections: page.sections.map(cloneSection),
  };
}

export function clonePages(pages: PageDoc[]): PageDoc[] {
  return pages.map(clonePage);
}

/**
 * **Fork on adopt.** A `StoreDoc` is created by deep-copying a theme's tokens and pages. After
 * this call the store has no live link back to the theme: editing the store cannot change the
 * theme, and — this is the part people ask about — improving a theme later cannot reach a store
 * that already adopted it. Theme "upgrades" are therefore *not* upgrade-safe, and there is no
 * migration path short of re-adopting, which replaces the pages wholesale and discards the
 * merchant's edits. That is the deliberate trade: a merchant's store can never be changed under
 * them by a release they did not ask for.
 *
 * Re-adopting keeps the brand (it is the merchant's, not the theme's) and replaces tokens and
 * pages. Pass the existing store's `brand` to do that.
 */
export function adoptTheme(theme: Theme, brand: StoreDoc["brand"]): StoreDoc {
  return {
    themeKey: theme.key,
    brand: { ...brand },
    tokens: cloneTokens(theme.tokens),
    pages: clonePages(theme.pages),
  };
}

/** A store's page for a route, or null when the store has no such page. */
export function pageForRoute(store: StoreDoc, route: string): PageDoc | null {
  return store.pages.find((p) => p.route === route) ?? null;
}
