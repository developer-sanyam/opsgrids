import type { ReactNode } from "react";
import { Link } from "react-router-dom";

import type { Category } from "@/api/types";
import { resolveImageSrc } from "./assetSrc";
import { ImagePlate, SectionEmpty, StoreHeading, StoreText } from "./primitives";
import { bool, choice, clampNum, optional } from "./settings";
import { T, step } from "./tokens";
import type { SectionRenderProps } from "./types";

const STYLES = ["tile", "list", "photo", "compact"] as const;
const ASPECTS = ["square", "landscape", "wide"] as const;

/**
 * The tile picture's box.
 *
 * Nothing wider than 16:9 is offered. `ImagePlate` covers its box and the bundled category art is
 * drawn 4:3 with the subject centred, so a wider frame crops the top and the bottom off the
 * silhouette — and the silhouette is the entire picture.
 */
const ASPECT_RATIO: Record<(typeof ASPECTS)[number], string> = {
  square: "1 / 1",
  landscape: "4 / 3",
  wide: "16 / 9",
};

/**
 * The column count past which a tile is too narrow to carry a sentence, per width.
 *
 * Four across at 390px leaves about fifty pixels of text inside the padding, which is a category
 * grid — not a card. Past these counts a tile drops its description, tightens its padding and
 * sets its label a step down, because the alternative is a label that wraps six times.
 */
const DENSE_FROM = { stacked: 3, wide: 5 } as const;

/**
 * Entry points into the catalogue. Each tile is one link, not a card with a link inside it.
 *
 * Four styles. `tile` and `list` are words only and are what the trade themes ship. `photo` and
 * `compact` carry the category's own picture — `photo` large with the label beneath it, `compact`
 * small on a tinted panel, which is the four-across grid a quick-commerce front page is built from.
 *
 * The picture comes from `Category.image_url` and never from numbered settings keys. Numbered keys
 * would bind a picture to a *position* in a list that `limit` slices, so raising the limit by one
 * would shuffle every merchant's photographs one tile to the left.
 */
export function CategoryTiles({ settings, categories, stacked, width }: SectionRenderProps) {
  const heading = optional(settings, "heading");
  const limit = clampNum(settings, "limit", 6, 1, 12);
  const columns = clampNum(settings, "columns", 3, 2, 6);
  const mobileColumns = clampNum(settings, "mobileColumns", 4, 2, 4);
  const showCount = bool(settings, "showCount", true);
  const showBlurb = bool(settings, "showBlurb", true);
  const style = choice(settings, "style", STYLES, "tile");
  const aspect = choice(settings, "aspect", ASPECTS, "landscape");
  const tint = optional(settings, "tint");
  const items = categories.slice(0, limit);

  /* A list is one column by definition. Everything else honours the count for the width it is at —
     which is the fix: `stacked` used to force one column, so a phone got a stack of wide cards
     where every reference design shows a grid. */
  const cols = style === "list" ? 1 : stacked ? mobileColumns : columns;
  const dense = cols >= (stacked ? DENSE_FROM.stacked : DENSE_FROM.wide);
  const withPicture = style === "photo" || style === "compact";
  const blurbShows = showBlurb && !dense && (style === "tile" || style === "photo");

  function countOf(category: Category): ReactNode {
    if (!showCount) return null;
    return (
      <span
        style={{
          fontFamily: T.fontBody,
          fontSize: "0.75rem",
          fontWeight: 700,
          color: T.accent,
          whiteSpace: "nowrap",
        }}
      >
        {category.line_count} {category.line_count === 1 ? "line" : "lines"}
      </span>
    );
  }

  function picture(category: Category, inset: boolean) {
    return (
      <ImagePlate
        seed={`cat-${category.key}`}
        src={resolveImageSrc(category.image_url)}
        style={{
          aspectRatio: ASPECT_RATIO[aspect],
          minHeight: 0,
          ...(inset
            ? { border: "none", borderRadius: `calc(${T.radius} / 1.5)`, background: "transparent" }
            : {}),
        }}
      />
    );
  }

  function tileBody(category: Category): ReactNode {
    const label = (
      <StoreHeading
        level={3}
        size={dense || withPicture ? "sm" : "md"}
        /* A four-across tile at 390px is about eighty pixels wide, and a category is called
           "Bearings & Power Transmission". Without this the unbreakable word runs straight out of
           the tile and over its neighbour — verified in Chrome, not guessed. */
        {...(dense ? { style: { overflowWrap: "anywhere" } } : {})}
      >
        {category.label}
      </StoreHeading>
    );

    if (style === "compact") {
      return (
        <>
          <span
            style={{
              display: "block",
              width: "100%",
              background: tint ?? T.surface,
              border: `1px solid ${T.border}`,
              borderRadius: T.radius,
              padding: step(0.4),
            }}
          >
            {picture(category, true)}
          </span>
          <span style={{ display: "flex", flexDirection: "column", gap: step(0.1), alignItems: "center" }}>
            {label}
            {countOf(category)}
          </span>
        </>
      );
    }

    if (style === "photo") {
      return (
        <>
          {picture(category, tint !== null)}
          <span style={{ display: "flex", flexDirection: "column", gap: step(0.15), alignItems: "center" }}>
            {label}
            {blurbShows && category.blurb ? (
              <StoreText muted small>
                {category.blurb}
              </StoreText>
            ) : null}
            {countOf(category)}
          </span>
        </>
      );
    }

    return (
      <>
        <span style={{ display: "flex", flexDirection: "column", gap: step(0.2) }}>
          {label}
          {blurbShows && category.blurb ? (
            <StoreText muted small>
              {category.blurb}
            </StoreText>
          ) : null}
        </span>
        {countOf(category)}
      </>
    );
  }

  /** The link's own box. Picture styles centre their words; word styles keep today's arrangement. */
  function tileStyle() {
    const shared = { height: "100%", textDecoration: "none", display: "flex" } as const;
    if (style === "compact") {
      return {
        ...shared,
        flexDirection: "column" as const,
        alignItems: "center" as const,
        gap: step(0.45),
        textAlign: "center" as const,
      };
    }
    if (style === "photo") {
      return {
        ...shared,
        flexDirection: "column" as const,
        gap: step(0.45),
        textAlign: "center" as const,
        background: tint ?? "transparent",
        borderRadius: T.radius,
        padding: tint !== null ? step(0.6) : 0,
      };
    }
    return {
      ...shared,
      flexDirection: style === "list" ? ("row" as const) : ("column" as const),
      alignItems: style === "list" ? ("center" as const) : ("flex-start" as const),
      justifyContent: "space-between" as const,
      gap: step(0.4),
      background: tint ?? T.surface,
      border: `1px solid ${T.border}`,
      borderRadius: T.radius,
      padding: dense ? step(0.6) : step(1),
    };
  }

  return (
    <section style={{ display: "flex", flexDirection: "column", gap: step(1.1), height: "100%" }}>
      {heading ? <StoreHeading width={width}>{heading}</StoreHeading> : null}
      {items.length === 0 ? (
        <SectionEmpty title="No categories yet" hint="Categories appear here once the catalogue has lines in them." />
      ) : (
        <ul
          style={{
            listStyle: "none",
            margin: 0,
            padding: 0,
            display: "grid",
            gridTemplateColumns: `repeat(${cols}, minmax(0, 1fr))`,
            gap: dense ? step(0.5) : step(0.7),
          }}
        >
          {items.map((category) => (
            <li key={category.key}>
              {/*
                The one hard-coded base left in this file, and it is hard-coded on purpose.

                `/store` is the console's framed preview; the buyer's store is served from `/live`.
                The renderer already solves this — `storeHref(route, base)` and `StoreRenderer`'s
                `basePath` — but that base stops at the chrome: `SectionRenderProps` carries
                `{settings, products, categories, stacked, width, mode}` and no base, so a section
                cannot build a correct in-store URL. **The fix is to thread the base onto
                `SectionRenderProps`** (types.ts → PageRenderer → SectionRenderer → StoreRenderer),
                which is four files owned elsewhere this cycle; it is in the handover instead of
                half-done here.

                Neither alternative works, both checked in Chrome rather than assumed:

                - A relative `to="catalogue"` resolves against the *matched* path, splat included,
                  so it is right on `/live` and `/store` and wrong on every page below them:
                  `/live/catalogue` → `/live/catalogue/catalogue`. React Router 7, verified.
                - Reading the location inside the section would be the second context
                  `render/types.ts` rules out, and would make the same component draw different
                  links in the editor's canvas and the gallery's miniature than it draws in a store.

                What this costs today, exactly: a plain click is correct on both surfaces, because
                the live route rewrites `/store/*` clicks to `/live/*` in the capture phase
                (`LiveStore.tsx`). What it does not cover — deliberately, since it must leave
                modified clicks alone — is ⌘-click, middle-click and "copy link address", which
                hand a buyer a console URL. That is the whole of the remaining defect.
              */}
              <Link to={`/store/catalogue?category=${category.key}`} style={tileStyle()}>
                {tileBody(category)}
              </Link>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
