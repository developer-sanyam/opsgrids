import type { CSSProperties } from "react";

import { ImagePlate, SectionEmpty, StoreHeading } from "./primitives";
import { bool, choice, clampNum, optional, str } from "./settings";
import { TileShell } from "./TileShell";
import { T, step } from "./tokens";
import type { SectionRenderProps } from "./types";

const SLOTS = [1, 2, 3, 4] as const;

const ASPECTS = ["wide", "square", "tall"] as const;

/** Unitless ratios, so a tile keeps its shape at every column count without a pixel anywhere. */
const ASPECT_RATIO: Record<(typeof ASPECTS)[number], string> = {
  wide: "3 / 2",
  square: "1 / 1",
  tall: "3 / 4",
};

/**
 * How much of the next tile shows past the edge of a scrolling row.
 *
 * A fifth of a tile is the whole point of the gesture: a row that ends exactly on the last visible
 * tile reads as a finished grid, and nobody swipes it.
 */
const PEEK = 0.2;

/** Below this canvas width a three-word tile title needs the smaller size to stay on two lines. */
const TIGHT_WIDTH = 420;

interface Slot {
  slot: number;
  title: string;
  image: string | null;
  href: string | null;
  tint: string | null;
}

/**
 * The reference's "Shop by store" row: up to four wide tiles to wherever the merchant chooses.
 *
 * **Not `category-tiles`.** That section is bound to the `categories` prop and can only ever show
 * what the catalogue contains. These four are authored by hand — a seasonal landing page, a brand,
 * an offer, a microsite on another domain — and none of them is necessarily a category at all.
 * Collapsing the two would mean either inventing categories to hold a campaign or teaching a
 * catalogue-bound section to ignore its own data.
 */
export function CollectionRow({ settings, stacked, width }: SectionRenderProps) {
  const heading = optional(settings, "heading");
  const columns = clampNum(settings, "columns", 3, 2, 4);
  const mobileColumns = clampNum(settings, "mobileColumns", 2, 1, 3);
  const aspect = choice(settings, "aspect", ASPECTS, "wide");
  const scroll = bool(settings, "scroll", false) && stacked;

  const items: Slot[] = SLOTS.map((slot) => ({
    slot,
    title: str(settings, `item${slot}Title`).trim(),
    image: optional(settings, `item${slot}Image`),
    href: optional(settings, `item${slot}Href`),
    tint: optional(settings, `item${slot}Tint`),
  })).filter((item) => item.title !== "");

  const perRow = stacked ? mobileColumns : columns;
  const gap = step(0.7);

  const tileStyle: CSSProperties = {
    display: "flex",
    flexDirection: "column",
    gap: step(0.45),
    height: "100%",
    textDecoration: "none",
    minWidth: 0,
  };

  return (
    <section style={{ display: "flex", flexDirection: "column", gap: step(1), height: "100%" }}>
      {heading ? <StoreHeading width={width}>{heading}</StoreHeading> : null}
      {items.length === 0 ? (
        <SectionEmpty
          title="No collections yet"
          hint="Give a tile a title in this section's settings — and a link, if it should go somewhere."
        />
      ) : (
        <ul
          {...(scroll
            ? {
                tabIndex: 0,
                "aria-label": heading ? `${heading} — scrollable row` : "Scrollable collection row",
              }
            : {})}
          style={
            scroll
              ? {
                  listStyle: "none",
                  margin: 0,
                  padding: `0 0 ${step(0.4)}`,
                  display: "grid",
                  /* The section is a column flex container, so this track's `min-width` would
                     otherwise resolve to `auto` — the sum of every tile — and the row would push
                     the whole page sideways instead of scrolling inside itself. Verified in
                     Chrome at 390: without this the document is 500px wide in a 390px frame. */
                  minWidth: 0,
                  maxWidth: "100%",
                  gridAutoFlow: "column",
                  // Each tile is one share of the visible width, with a fifth of the next showing.
                  gridAutoColumns: `calc((100% - ${gap} * ${perRow}) / ${perRow + PEEK})`,
                  gap,
                  overflowX: "auto",
                  scrollSnapType: "x proximity",
                }
              : {
                  listStyle: "none",
                  margin: 0,
                  padding: 0,
                  display: "grid",
                  gridTemplateColumns: `repeat(${Math.min(perRow, items.length)}, minmax(0, 1fr))`,
                  gap,
                }
          }
        >
          {items.map((item) => (
            <li key={item.slot} style={{ minWidth: 0, ...(scroll ? { scrollSnapAlign: "start" } : {}) }}>
              <TileShell href={item.href} style={tileStyle}>
                <span
                  style={{
                    display: "block",
                    background: item.tint ?? T.surface,
                    border: `1px solid ${T.border}`,
                    borderRadius: T.radius,
                    aspectRatio: ASPECT_RATIO[aspect],
                    padding: step(0.5),
                    overflow: "hidden",
                  }}
                >
                  <ImagePlate
                    seed={`collection-${item.slot}-${item.title}`}
                    src={item.image}
                    style={{
                      height: "100%",
                      minHeight: 0,
                      border: "none",
                      borderRadius: `calc(${T.radius} / 2)`,
                      objectFit: "contain",
                    }}
                  />
                </span>
                <span
                  style={{
                    fontFamily: T.fontBody,
                    fontSize: width < TIGHT_WIDTH ? "0.8125rem" : "0.875rem",
                    fontWeight: 700,
                    lineHeight: 1.3,
                    color: T.text,
                    textWrap: "balance",
                  }}
                >
                  {item.title}
                </span>
              </TileShell>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
