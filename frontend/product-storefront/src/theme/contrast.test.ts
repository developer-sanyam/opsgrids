/**
 * Every shipped theme, measured.
 *
 * A theme is twelve tokens, and the model quietly assumes things about them that nothing checked.
 * The sharpest of those assumptions is that the **accent is legible as text on the page ground** —
 * three places paint `T.accent` straight onto `T.bg`: `Eyebrow`, the active nav link in the store
 * chrome, and the line count under a category tile. Nothing in the type system says an accent has
 * to survive that, and the obvious accent for a quick-commerce grocer is a brand yellow, which on
 * white is about 1.6:1. All three would have shipped unreadable.
 *
 * So this file is arithmetic, not rendering. No DOM, no jsdom, no components: the tokens are hex
 * strings on an object, and WCAG 2.1 says exactly what to do with two of them. That makes it the
 * cheapest test in the suite and the one most likely to catch a palette change before a buyer
 * does — a theme is edited by eye, and an eye adapts to a screen in a way a ratio does not.
 *
 * The maths is written out rather than pulled from a package. It is fifteen lines of a published
 * formula that has not changed since 2008; a dependency for it would be a supply-chain surface and
 * a version to keep, in exchange for nothing.
 *
 * **Scope, stated honestly.** This measures the five pairs the primitives actually paint from the
 * theme's own tokens. It cannot measure a pair where one side is merchant data — a `promo-banner`
 * tint, a `hero` image, a colour typed into a `color` control — because that side does not exist
 * until a merchant types it. Those grounds are handled structurally instead: `bandGround` returns
 * `T.text` on a tint, and an action on a ground the theme does not own takes `tone="inherit"`.
 */

import { describe, expect, it } from "vitest";

import type { ThemeTokens } from "./schema";
import { THEMES } from "./themes";

/* ------------------------------- the formula ------------------------------ */

/**
 * The 8-bit channels of a hex colour.
 *
 * It throws rather than falling back, and that is the point: a token written as `rgb(11 122 36)`
 * or `tomato` is perfectly valid CSS and would render fine, but a parser that shrugged and
 * returned black would report a beautiful ratio for a colour it never looked at. A theme that
 * stops being hex should fail this test loudly and be dealt with, not pass by accident.
 */
function channels(hex: string): [number, number, number] {
  const short = /^#([0-9a-f])([0-9a-f])([0-9a-f])$/i.exec(hex);
  if (short) {
    return [short[1]!, short[2]!, short[3]!].map((d) => parseInt(d + d, 16)) as [
      number,
      number,
      number,
    ];
  }
  const long = /^#([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i.exec(hex);
  if (!long) throw new Error(`not a hex colour this test can measure: ${JSON.stringify(hex)}`);
  return [long[1]!, long[2]!, long[3]!].map((pair) => parseInt(pair, 16)) as [number, number, number];
}

/**
 * WCAG 2.1 relative luminance.
 *
 * The transfer curve is the reason this cannot be done with a channel average: sRGB is encoded for
 * the eye, not for light, so a mid-grey byte carries about a fifth of the light a white one does
 * rather than half. Skipping the curve is the classic way to build a contrast checker that passes
 * pairs nobody can read.
 *
 * The green weight is 0.7152 because the eye is built that way — which is also why a saturated
 * yellow, nearly all green by luminance, is so hard to put text on.
 */
function luminance(hex: string): number {
  const [r, g, b] = channels(hex).map((channel) => {
    const c = channel / 255;
    return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  }) as [number, number, number];
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

/**
 * The contrast ratio between two colours, from 1 to 21.
 *
 * Order does not matter — the brighter one goes on top — so a caller never has to know which of a
 * pair is the ground. The `0.05` is the flare term: it models the light a real screen throws back
 * at a reader in a real room, and it is what stops two near-blacks from scoring infinity.
 */
function contrastRatio(a: string, b: string): number {
  const [light, dark] = [luminance(a), luminance(b)].sort((x, y) => y - x) as [number, number];
  return (light + 0.05) / (dark + 0.05);
}

/** Two decimals, for a failure message a human reads. Never for a comparison. */
function readable(ratio: number): string {
  return `${Math.round(ratio * 100) / 100}:1`;
}

/* --------------------------------- the bar -------------------------------- */

/**
 * WCAG 2.1 AA for text below 18.66px bold / 24px regular.
 *
 * Every pair below is small text somewhere: the eyebrow is 12px, a meta tag is 12px, the tile line
 * count is small, and the AA large-text allowance of 3:1 would let all of them through. So the bar
 * is the small-text one for all five, which is the only bar that is true everywhere the pair is
 * used.
 */
const AA_SMALL_TEXT = 4.5;

/**
 * The five pairs the primitives put on screen out of the theme's own tokens.
 *
 * Each names a real call site, because a pair with no call site is a rule nobody agreed to and the
 * first person to fail it will — correctly — want to know who is asking.
 */
const PAIRS: ReadonlyArray<{
  name: string;
  fg: keyof ThemeTokens;
  bg: keyof ThemeTokens;
  where: string;
}> = [
  {
    name: "text on bg",
    fg: "colorText",
    bg: "colorBg",
    where: "every heading and every paragraph in the page flow",
  },
  {
    name: "muted on bg",
    fg: "colorMuted",
    bg: "colorBg",
    where: "StoreText muted, a section's empty-state hint, a form's help line",
  },
  {
    name: "muted on surface",
    fg: "colorMuted",
    bg: "colorSurface",
    where: "MetaTag's quiet tone and the struck MRP, both of which sit inside a card",
  },
  {
    name: "accentText on accent",
    fg: "colorAccentText",
    bg: "colorAccent",
    where: "every solid action, the discount flag, the quantity stepper",
  },
  {
    /*
     * The one this file was written for.
     *
     * It is the only pair where the two tokens were never designed against each other: an accent
     * is picked to stand out, a ground is picked to disappear, and `colorAccentText` absorbs the
     * consequences everywhere *except* here, where the accent is the text and there is no paired
     * colour to lean on.
     */
    name: "accent on bg",
    fg: "colorAccent",
    bg: "colorBg",
    where: "Eyebrow, the active nav link, and CategoryTiles' line count — accent AS TEXT",
  },
];

/* --------------------------------- guards --------------------------------- */

describe("the ratio itself", () => {
  it("puts the two ends of the scale where WCAG does", () => {
    expect(contrastRatio("#000000", "#FFFFFF")).toBeCloseTo(21, 5);
    expect(contrastRatio("#FFFFFF", "#FFFFFF")).toBeCloseTo(1, 5);
  });

  it("does not care which colour is named first", () => {
    expect(contrastRatio("#0B7A24", "#FFFFFF")).toBeCloseTo(contrastRatio("#FFFFFF", "#0B7A24"), 10);
  });

  /**
   * The published boundary. `#767676` on white is the canonical grey that just passes AA and
   * `#777777` the one that just fails — a pair of numbers that pins the transfer curve, the
   * luminance weights and the flare term all at once. Get any of the three wrong and one of these
   * two moves.
   */
  it("lands on the known 4.5 boundary from both sides", () => {
    expect(contrastRatio("#767676", "#FFFFFF")).toBeGreaterThanOrEqual(AA_SMALL_TEXT);
    expect(contrastRatio("#777777", "#FFFFFF")).toBeLessThan(AA_SMALL_TEXT);
  });

  /**
   * Weighting the channels equally is the mistake this catches. A saturated yellow is a wall of
   * luminance — around 18:1 on black, nowhere near passing on white — and a naive checker reports
   * it as a middling colour in both directions.
   */
  it("reads a saturated yellow as the near-white it is", () => {
    expect(contrastRatio("#F7CB45", "#FFFFFF")).toBeLessThan(2);
    expect(contrastRatio("#F7CB45", "#000000")).toBeGreaterThan(10);
  });

  it("refuses a token it cannot actually measure", () => {
    expect(() => contrastRatio("tomato", "#FFFFFF")).toThrow(/hex/);
    expect(() => contrastRatio("rgb(11 122 36)", "#FFFFFF")).toThrow(/hex/);
  });
});

/* ------------------------------- every theme ------------------------------ */

/**
 * `describe.each(THEMES)` on purpose, not a hand-written list: a theme added tomorrow is measured
 * the moment it is exported, with nothing to remember. The count below is the only thing that has
 * to be maintained, and it exists so that a theme silently dropped from the barrel file cannot
 * make this whole suite pass by measuring nothing.
 */
const SHIPPED_THEMES = 8;

describe("the shipped themes", () => {
  it(`still numbers ${SHIPPED_THEMES}, so nothing has quietly stopped being measured`, () => {
    expect(THEMES).toHaveLength(SHIPPED_THEMES);
  });
});

describe.each(THEMES.map((theme) => [theme.key, theme] as const))("%s", (key, theme) => {
  it.each(PAIRS.map((pair) => [pair.name, pair] as const))(
    "reads %s at AA or better",
    (_name, pair) => {
      const fg = theme.tokens[pair.fg] as string;
      const bg = theme.tokens[pair.bg] as string;
      const ratio = contrastRatio(fg, bg);

      /* The message is the deliverable. Whoever breaks this will be halfway through changing a
         palette by eye, and "expected 3.1 to be >= 4.5" tells them nothing; the theme, the pair,
         the two hexes, the measured number and the call site tell them everything. */
      expect(
        ratio,
        `${key}: ${pair.name} is ${readable(ratio)} (${fg} on ${bg}), below AA ${AA_SMALL_TEXT}:1.\n` +
          `  Painted by: ${pair.where}.\n` +
          `  Fix the palette, not this threshold.`,
      ).toBeGreaterThanOrEqual(AA_SMALL_TEXT);
    },
  );
});
