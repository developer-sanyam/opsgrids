// The design-token rules: feature code names a token, never a value.
//
// frontend/CLAUDE.md is plain about it — no hex colours, no arbitrary spacing, no off-scale sizes,
// no one-off shadows or radii — and docs/theme-guide.md gives the reason: light and dark are both
// defined at token level, so a raw value looks right in whichever mode it was written in and wrong
// in the other, and nobody notices until a customer does. Until now nothing enforced it (gate row
// 18, "token lint", was reported N/A). This is that row's first tooling, and it starts where the
// rule is already true: the operator console adopts it now, and an app adopts it by spreading this
// array after the shared preset in its `eslint.config.js`. It is opt-in per app on purpose — turning
// it on for an app that has raw values in it is a migration, not a lint fix.
//
//     import preset from "@opsgrids/config/eslint";
//     import tokens from "@opsgrids/config/eslint-tokens";
//     export default [...preset, ...tokens];
//
// What it refuses, in the strings and templates of an app's own code:
//
//   * a raw colour — `#6c4cf1`, `rgb(…)`, `hsl(…)`, `oklch(…)` — anywhere. A colour is a token
//     (`bg-accent`, `var(--color-accent)`); the palette lives in `@opsgrids/ui`'s stylesheet.
//   * a Tailwind arbitrary value — `w-[256px]`, `text-[13px]`, `bg-[#fff]`, `p-[7px]`. The scale is
//     the design (4 · 8 · 12 · 16 · 24 · 32 · 48 · 64), and a bracket steps off it. A custom
//     property is the way to carry a computed number: `w-(--share)` with `--share` set inline.
//   * a raw length in a string — `12px`, `1.5rem`, `50vh` — for the same reason, wherever it hides.
//   * an inline style that sets anything but a CSS custom property. `style={{ "--x": "40%" }}` is
//     allowed, because the class that reads it still decides what the number means; `style={{ width }}`
//     is not, because nothing themes it, densifies it or overrides it.
//
// Not covered, by design: the design system's own package (it *defines* the tokens, so it has to
// spell values), tests and their helpers (they assert on classes and fixtures), and numbers in code
// (an SVG's coordinates are geometry, not style).

/**
 * A raw colour: a hex with a length that is a colour, or a colour function.
 *
 * A short hex must contain a letter — `#fff`, `#a3c` — because `#4821` in a support-case reference
 * is a number, not a colour, and prose is where this rule would otherwise cry wolf. A six- or
 * eight-digit run is a colour wherever it appears.
 */
const COLOUR = String.raw`(?:#(?:(?=[0-9]*[a-fA-F])[0-9a-fA-F]{3,4}\b|[0-9a-fA-F]{6}\b|[0-9a-fA-F]{8}\b)|\b(?:rgb|rgba|hsl|hsla|hwb|lab|lch|oklab|oklch)\()`;

/**
 * A Tailwind arbitrary *value*: `<utility>-[…]`, where the utility is not one of the variant
 * prefixes that legitimately take brackets (`data-[state=open]:`, `aria-[sort=ascending]:`).
 */
const ARBITRARY_VALUE = String.raw`(?:^|[\s:!])(?!(?:data|aria|group|peer|has|supports|in|not|nth)-)-?[a-z][a-z0-9]*(?:-[a-z0-9]+)*-\[`;

/** A length with a unit that is a size on screen. */
const RAW_LENGTH = String.raw`(?:^|[^\w.#-])\d+(?:\.\d+)?(?:px|rem|em|vh|vw|dvh|svh)\b`;

/** A selector that matches a string literal, or a piece of a template literal, by its text. */
function textMatching(pattern) {
  return `:matches(Literal[value=/${pattern}/], TemplateElement[value.raw=/${pattern}/])`;
}

export const tokenRules = {
  "no-restricted-syntax": [
    "error",
    {
      selector: textMatching(COLOUR),
      message:
        "A raw colour in feature code. Use a token: a Tailwind class (`bg-accent`, `text-muted`) or `var(--color-…)`. Colours are defined once, in @opsgrids/ui, for both themes.",
    },
    {
      selector: textMatching(ARBITRARY_VALUE),
      message:
        "A Tailwind arbitrary value (`w-[…]`). Step onto the scale (4 · 8 · 12 · 16 · 24 · 32 · 48 · 64), or carry a computed number in a custom property (`w-(--share)` with `style={{ '--share': … }}`).",
    },
    {
      selector: textMatching(RAW_LENGTH),
      message:
        "A raw length (`12px`, `1.5rem`) in feature code. Use the spacing and type scale, or a token.",
    },
    {
      selector:
        "JSXAttribute[name.name='style'] ObjectExpression > Property[key.type='Identifier']",
      message:
        "An inline style that sets a real CSS property. Style with classes that read tokens; an inline style may only set a custom property (`{ '--share': '40%' }`).",
    },
    {
      selector:
        "JSXAttribute[name.name='style'] ObjectExpression > Property[key.type='Literal']:not([key.value=/^--/])",
      message:
        "An inline style that sets a real CSS property. Style with classes that read tokens; an inline style may only set a custom property (`{ '--share': '40%' }`).",
    },
  ],
};

/**
 * The flat-config blocks: the rules on an app's own source, and off in its tests and helpers.
 * Spread after the shared preset, so the parser and plugins are already in place.
 */
export default [
  {
    files: ["src/**/*.{ts,tsx}"],
    ignores: [
      "**/*.{test,spec}.{ts,tsx}",
      "**/test-setup.{ts,tsx}",
      "**/testing.{ts,tsx}",
      "**/testing-render.{ts,tsx}",
      "**/contracts.ts",
    ],
    rules: tokenRules,
  },
];
