/**
 * Turning a theme into a store, and keeping the two apart afterwards.
 */

import type {
  NavPlacement,
  PageDoc,
  SectionInstance,
  StoreDoc,
  StoreMode,
  Theme,
  ThemeTokens,
} from "./schema";

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
    // Spread rather than assign: `nav` is optional and `exactOptionalPropertyTypes` will not take
    // an explicit `undefined`. A theme that says nothing keeps saying nothing, which is what makes
    // `navPlacement`'s default the same answer before and after an adopt.
    ...(page.nav !== undefined ? { nav: page.nav } : {}),
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
    // Copied, not looked up later through `themeByKey(store.themeKey)`: that would reinstate the
    // live theme link this fork exists to cut, and it has nothing to return for a store carrying a
    // theme key this build no longer ships.
    mode: theme.mode,
    brand: { ...brand },
    tokens: cloneTokens(theme.tokens),
    pages: clonePages(theme.pages),
  };
}

/**
 * The store's mode, narrowed.
 *
 * Render code calls this rather than reading `store.mode` directly: a `StoreDoc` can arrive off
 * the wire, or out of a document written before this field existed, so the value is only
 * `StoreMode` by type and not by evidence. Trade is the safe default — a trade store missing an
 * ADD button is boring, a retail store missing its MOQ is wrong.
 */
export function resolveMode(store: Pick<StoreDoc, "mode">): StoreMode {
  return store.mode === "retail" ? "retail" : "trade";
}

/** A store's page for a route, or null when the store has no such page. */
export function pageForRoute(store: StoreDoc, route: string): PageDoc | null {
  return store.pages.find((p) => p.route === route) ?? null;
}

/**
 * Where a page shows up in the store's chrome, narrowed.
 *
 * Read through here, never off `page.nav` directly: the field is optional and a document can
 * arrive off the wire or out of a build that predates it, so the value is `NavPlacement` by type
 * and not by evidence. **Absent means `"primary"`** — the behaviour every store had before this
 * field existed, so nothing already published moves.
 */
export function navPlacement(page: Pick<PageDoc, "nav">): NavPlacement {
  if (page.nav === "footer") return "footer";
  if (page.nav === "none") return "none";
  return "primary";
}

/**
 * The store's pages that belong in one part of the chrome, in page order.
 *
 * Page order *is* nav order — there is no second ordering to keep in step with it, which is why
 * `reorder-pages` in the editor is the only control a merchant needs to arrange the bar.
 */
export function pagesInNav(store: Pick<StoreDoc, "pages">, placement: NavPlacement): PageDoc[] {
  return store.pages.filter((page) => navPlacement(page) === placement);
}
