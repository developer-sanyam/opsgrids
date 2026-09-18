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

export interface PageDoc {
  id: string;
  /** "/", "/catalogue", "/product", "/about", "/contact" */
  route: string;
  name: string;
  sections: SectionInstance[];
}

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
  tokens: ThemeTokens;
  /** The starting templates. Copied into a `StoreDoc` on adopt; never read after that. */
  pages: PageDoc[];
}

/** One merchant's store — a COPY of a theme, then edited. */
export interface StoreDoc {
  themeKey: string;
  brand: { name: string; tagline: string; logoText: string };
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
