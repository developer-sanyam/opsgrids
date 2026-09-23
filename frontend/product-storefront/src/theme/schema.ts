/**
 * The storefront document schema — the contract between the three halves of this product: the
 * theme library, the buyer-facing renderer, and the merchant's drag-and-drop editor.
 *
 * **These names are fixed.** The editor codes against them; renaming a field here breaks it. Add
 * fields, do not rename them.
 *
 * The canvas is a **12-column grid**. `layout.x` is 0–11, `layout.w` is 1–12, and `y`/`h` are row
 * units where one row is 40px at desktop. Below `STACK_WIDTH` every section becomes full width and
 * the page stacks by ascending `y`. Free absolute pixel positioning is deliberately not the model:
 * a grid is what keeps "place it anywhere" responsive instead of producing a store that only works
 * at the width the merchant happened to design it at.
 */

/** Every setting a merchant can change is one of these. Deliberately flat — no nested objects, so
 *  the editor can render one control per field and diff a section by shallow compare. */
export type SettingValue = string | number | boolean | null;

export interface SettingField {
  key: string;
  label: string;
  type: "text" | "textarea" | "number" | "select" | "toggle" | "color" | "image" | "product-source";
  options?: { value: string; label: string }[];
  default: SettingValue;
  help?: string;
}

export interface SectionDef {
  type: string;
  /** Human label for the editor's palette. */
  name: string;
  group: "content" | "commerce" | "social" | "layout";
  fields: SettingField[];
  /** The size a freshly dropped instance takes, in grid units. */
  defaultLayout: { w: number; h: number };
}

export interface SectionInstance {
  id: string;
  type: string;
  settings: Record<string, SettingValue>;
  layout: { x: number; y: number; w: number; h: number };
}

/**
 * Where a page appears in the store's chrome.
 *
 * A ~120-page sitemap is the reason this exists: `StoreRenderer` used to put every page in
 * `store.pages` into the top navigation, which is a readable bar at three pages and an unusable
 * one at thirty-five. Legal pages belong at the bottom, most company pages belong in a group, and
 * some pages — a thank-you page, a campaign landing page — should only be reachable from a link
 * somewhere else.
 *
 * **Optional on purpose.** Absent means `"primary"`, so every theme this build ships and every
 * store document written before this field existed renders exactly as it did.
 *
 * This is the chrome's *automatic* navigation: the store's own list of pages, placed. It is not
 * the only way a link reaches a footer — the `footer-columns` section lets a merchant hand-author
 * whole link columns, including links to pages set to `"none"` here, plus addresses this document
 * knows nothing about (a PDF, a WhatsApp number, an external portal). The two are deliberately
 * different mechanisms and neither subsumes the other: `nav` answers "where does *this page* show
 * up", `footer-columns` answers "what does *this footer* say". Where both land on one page the
 * section owns the `contentinfo` — one page, one footer landmark — and the chrome's footer nav
 * drops below it as a legal strip. Do not build a third mechanism.
 */
export type NavPlacement = "primary" | "footer" | "none";

export interface PageDoc {
  id: string;
  /** "/", "/catalogue", "/product", "/about", "/contact" */
  route: string;
  name: string;
  /** Absent means `"primary"`. Read it through `navPlacement`, never raw. */
  nav?: NavPlacement;
  sections: SectionInstance[];
}

/**
 * What kind of buyer this store serves. Retail buyers add to a cart; trade buyers build a line
 * list and enquire. It is a property of the store, never of a page or a section.
 *
 * It is deliberately **not** a `ThemeTokens` member: `TOKEN_VARS` is
 * `satisfies Record<keyof ThemeTokens, string>`, so a thirteenth member would force a meaningless
 * `--st-mode` custom property that no section reads, and `ThemeTokenPanel` would offer the
 * merchant a colour picker for it.
 */
export type StoreMode = "retail" | "trade";

export interface ThemeTokens {
  colorBg: string;
  colorSurface: string;
  colorText: string;
  colorMuted: string;
  colorAccent: string;
  colorAccentText: string;
  colorBorder: string;
  fontHeading: string;
  fontBody: string;
  /** Corner radius in px. */
  radius: number;
  /** The spacing step in px — gutters and internal padding are multiples of it. */
  space: number;
  /** The content column's maximum width in px. */
  maxWidth: number;
}

export interface Theme {
  key: string;
  name: string;
  description: string;
  industry: string;
  /** Retail or trade. Copied onto the store at adopt time; never read back off the theme after. */
  mode: StoreMode;
  tokens: ThemeTokens;
  /** The starting templates. Copied into a `StoreDoc` on adopt; never read after that. */
  pages: PageDoc[];
}

/**
 * A store's name, tagline and header wordmark, as rendered.
 *
 * **Not part of `StoreDoc`.** ADR-0011 moved these off the theme document and onto the store's
 * own identity (`StoreOut.name` / `.tagline` / `.logo_text`) — a merchant's name is theirs, not a
 * property of the theme they happened to pick, so re-adopting a theme can no longer carry it
 * through a copy step. Every screen that draws chrome takes this as its own prop, resolved from
 * the identity row (`@/settings/api`'s `brandFromIdentity`) with the theme's own suggestion as the
 * fallback before a merchant has set one (`@/theme/themes`' `suggestedBrand`).
 */
export interface Brand {
  name: string;
  tagline: string;
  logoText: string;
}

/** One merchant's store — a COPY of a theme, then edited. */
export interface StoreDoc {
  themeKey: string;
  /**
   * Copied from the theme by `adoptTheme`, not resolved from `themeKey` — resolving would
   * reinstate the live theme link the fork deliberately cuts, and would break for a store carrying
   * a theme key this build no longer ships. Render code reads it through `resolveMode`.
   */
  mode: StoreMode;
  tokens: ThemeTokens;
  pages: PageDoc[];
}

/** The canvas is 12 columns wide, always. */
export const GRID_COLUMNS = 12;

/** One `layout` row unit, in px, at desktop. */
export const ROW_HEIGHT = 40;

/**
 * Below this canvas width the grid collapses: every section spans the full width and the page
 * stacks in ascending `y`. Measured against the *canvas*, not the viewport, so the editor's device
 * preview and a genuinely narrow browser behave identically.
 */
export const STACK_WIDTH = 768;

/**
 * The one setting key the page layer itself understands. A section declaring this field and
 * setting it true spans the full canvas width instead of its grid columns.
 *
 * Named here so it is not a magic string in two files: the section that declares the field and
 * `PageRenderer`, which reads it off the instance. Everything else in `settings` belongs to the
 * section alone — the page layer must never grow a second well-known key without a decision.
 */
export const BLEED_SETTING = "bleed";
