/// <reference types="vite/client" />
/**
 * Contrast is checked where it is defined.
 *
 * Reads `console.css` itself — the light `@theme` block and the dark override — and asserts every
 * foreground/background pairing the components actually draw, in both themes. Change a token value
 * and this names the pairing it broke, before a screenshot or an axe run ever sees it.
 *
 * Thresholds are WCAG 2.x: 4.5:1 for text (1.4.3), 3:1 for the edge or fill that identifies a
 * control (1.4.11). `disabled` is exempt by the standard and is not listed.
 */

import { describe, expect, it } from "vitest";

// Text, not styles: `vitest.config.ts` lists this file under `css.include` so the import is not blanked.
import css from "./console.css?raw";

type Palette = Record<string, string>;

/** `--color-name: #hex;` declarations inside the first block that `selector` opens. */
function colourBlock(source: string, selector: string): Palette {
  const start = source.indexOf(selector);
  if (start === -1) throw new Error(`console.css has no ${selector} block`);
  const open = source.indexOf("{", start);
  const close = source.indexOf("\n}", open);
  const body = source.slice(open + 1, close);
  const palette: Palette = {};
  for (const match of body.matchAll(/--color-([a-z-]+):\s*(#[0-9a-f]{6});/gi)) {
    const [, name, value] = match;
    if (name !== undefined && value !== undefined) palette[name] = value.toLowerCase();
  }
  return palette;
}

const light = colourBlock(css, "@theme {");
/** Dark overrides only what changes; everything else is inherited from the light block. */
const dark: Palette = { ...light, ...colourBlock(css, ':root[data-theme="dark"] {') };

function luminance(hex: string): number {
  const channels = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255);
  const [r = 0, g = 0, b = 0] = channels.map((c) =>
    c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4,
  );
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

function contrast(a: string, b: string): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x) as [number, number];
  return (hi + 0.05) / (lo + 0.05);
}

const TEXT = 4.5;
const EDGE = 3;

/** [foreground, background, minimum, where it is drawn] */
const PAIRS: ReadonlyArray<readonly [string, string, number, string]> = [
  // Body copy on every surface a page has.
  ["ink", "ground", TEXT, "page text"],
  ["ink", "surface", TEXT, "card text"],
  ["ink", "elevated", TEXT, "menu and dialog text"],
  ["ink", "sunken", TEXT, "table header, hovered row"],
  ["ink-soft", "surface", TEXT, "labels, secondary headings"],
  ["ink-soft", "sunken", TEXT, "ghost button on hover"],
  ["muted", "ground", TEXT, "page descriptions"],
  ["muted", "surface", TEXT, "helper text, card subtitles"],
  ["muted", "elevated", TEXT, "menu descriptions"],
  ["muted", "sunken", TEXT, "table header labels"],
  ["faint", "ground", TEXT, "captions on the page"],
  ["faint", "surface", TEXT, "captions, placeholders, nav group titles"],
  ["faint", "elevated", TEXT, "captions in menus and dialogs"],
  ["faint", "sunken", TEXT, "captions in a recessed area"],
  ["faint", "accent-soft", TEXT, "count beside an active nav row"],
  // Accent as text, and text on the accent.
  ["accent", "surface", TEXT, "links"],
  ["accent", "ground", TEXT, "links on the page"],
  ["accent-strong", "surface", TEXT, "link hover"],
  ["accent-ink", "accent-soft", TEXT, "active nav row, accent tile"],
  ["accent-ink", "surface", TEXT, "text buttons"],
  ["on-accent", "accent", TEXT, "primary button, active pill"],
  ["on-accent", "accent-strong", TEXT, "primary button hover"],
  ["on-negative", "negative", TEXT, "destructive button"],
  ["surface", "ink", TEXT, "dark card, dark button"],
  // Semantic and status inks on their tints and on plain surfaces.
  ["positive", "positive-soft", TEXT, "rising delta"],
  ["positive", "surface", TEXT, "positive trend text"],
  ["negative", "negative-soft", TEXT, "falling delta"],
  ["negative", "surface", TEXT, "field errors, required asterisk"],
  ["negative", "elevated", TEXT, "field errors in a dialog"],
  ["present", "present-soft", TEXT, "success badge"],
  ["present", "surface", TEXT, "success text"],
  ["late", "late-soft", TEXT, "warning badge, sample chip"],
  ["late", "surface", TEXT, "warning text"],
  ["danger", "danger-soft", TEXT, "danger badge"],
  ["danger", "surface", TEXT, "danger text"],
  ["info", "info-soft", TEXT, "info badge"],
  ["info", "surface", TEXT, "info text"],
  ["cyan", "cyan-soft", TEXT, "cyan icon tile"],
  ["cyan", "surface", TEXT, "cyan series label"],
  ["neutral", "neutral-soft", TEXT, "neutral badge, neutral tag"],
  ["neutral", "surface", TEXT, "neutral text"],
  // An inline alert's body and reference line sit on its tint.
  ["ink-soft", "info-soft", TEXT, "info alert body"],
  ["ink-soft", "late-soft", TEXT, "warning alert body"],
  ["ink-soft", "negative-soft", TEXT, "danger alert body"],
  ["ink-soft", "positive-soft", TEXT, "success alert body"],
  ["muted", "info-soft", TEXT, "info alert reference"],
  ["muted", "late-soft", TEXT, "warning alert reference"],
  ["muted", "negative-soft", TEXT, "danger alert reference"],
  ["muted", "positive-soft", TEXT, "success alert reference"],
  // Edges and fills that identify a control.
  ["field-edge", "surface", EDGE, "input, select, checkbox and secondary-button edge"],
  ["field-edge", "ground", EDGE, "a field placed on the page"],
  ["field-edge", "elevated", EDGE, "a field in a dialog or menu"],
  ["field-edge", "sunken", EDGE, "a field in a recessed toolbar"],
  ["surface", "field-edge", EDGE, "switch thumb on the off track"],
  ["surface", "accent", EDGE, "switch thumb on the on track"],
  ["accent", "surface", EDGE, "checked control, active indicator"],
  ["focus", "surface", EDGE, "focus ring on a card"],
  ["focus", "ground", EDGE, "focus ring on the page"],
  ["focus", "elevated", EDGE, "focus ring in a dialog"],
];

/** `--color-focus` is `var(--color-accent)`; resolve it the way the browser does. */
function resolve(palette: Palette, name: string): string {
  if (name === "focus") return resolve(palette, "accent");
  const value = palette[name];
  if (value === undefined) throw new Error(`--color-${name} is not a hex token in console.css`);
  return value;
}

describe.each([
  ["light", light],
  ["dark", dark],
] as const)("%s theme contrast", (_theme, palette) => {
  it.each(PAIRS)("%s on %s is at least %s:1 (%s)", (fg, bg, min) => {
    expect(contrast(resolve(palette, fg), resolve(palette, bg))).toBeGreaterThanOrEqual(min);
  });
});

describe("the token file", () => {
  it("defines every colour the pairs use, in both themes", () => {
    const names = new Set(PAIRS.flatMap(([fg, bg]) => [fg, bg]).filter((n) => n !== "focus"));
    for (const name of names) {
      expect(light[name], `light --color-${name}`).toMatch(/^#[0-9a-f]{6}$/);
      expect(dark[name], `dark --color-${name}`).toMatch(/^#[0-9a-f]{6}$/);
    }
  });

  it("gives the dark theme its own value for every colour that is drawn as text or a fill", () => {
    const own = colourBlock(css, ':root[data-theme="dark"] {');
    const inherited = Object.keys(light).filter((name) => !(name in own));
    expect(inherited).toEqual([]);
  });

  it("draws the global focus ring from the base layer, so a component's own focus style wins", () => {
    const base = css.slice(css.indexOf("@layer base {"));
    expect(base).toMatch(/^@layer base \{\s*:where\(a, button, input, select, textarea, \[tabindex\]\):focus-visible/);
  });

  it("keeps Tailwind's focus:outline-none + focus-visible:outline-2 pairing drawing a ring", () => {
    // Unlayered, and only the variable: see the comment on the rule in console.css.
    expect(css).toMatch(/\n:focus-visible \{\s*--tw-outline-style: solid;\s*\}/);
  });

  it("caps font weight at 700", () => {
    expect(css).toMatch(/--font-weight-extrabold:\s*700;/);
    expect(css).toMatch(/--font-weight-black:\s*700;/);
  });
});
