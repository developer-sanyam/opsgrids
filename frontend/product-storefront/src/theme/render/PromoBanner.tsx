import { BLEED_SETTING, STACK_WIDTH } from "../schema";
import { pageGutter } from "./pageGrid";
import { ActionLink, Eyebrow, ImagePlate, StoreHeading, StoreText } from "./primitives";
import { bool, optional, str } from "./settings";
import { TileShell } from "./TileShell";
import { T, step } from "./tokens";
import type { SectionRenderProps } from "./types";

const TILES = [1, 2, 3, 4] as const;

/** Above this the illustration sits beside the words instead of above the tiles. */
const SPLIT_WIDTH = 720;

/**
 * The tiles are always four across and never wider than this, at any canvas width.
 *
 * Four across is the reference — at 390 that is an ~80px tile, which is exactly what it shows.
 * The cap matters at the other end: inside a 1280px banner, four equal fractions would be 170px
 * squares, and four large photographs inside a promo banner is a second product grid rather than
 * the set of small shortcuts this is.
 */
const TILES_ACROSS = 4;
const TILE_ROW_MAX = 22;

/**
 * The pastel banner: a headline, a sub-line, an illustration and up to four small tiles inside it.
 *
 * **The heading is an `h2`, always.** A page carries exactly one `h1` and the hero owns it. A
 * banner two thirds of the way down a page that also claims `h1` gives a screen-reader user two
 * answers to "what is this page", and the outline is the one thing on a store that cannot be
 * repaired by looking at it.
 *
 * Everything paints `T.text` on the tint. The tint is merchant data and may be any colour, so the
 * accent — which a theme guarantees only against its *own* backgrounds — cannot be trusted here;
 * the eyebrow inherits instead of reaching for it.
 */
export function PromoBanner({ settings, width }: SectionRenderProps) {
  const eyebrow = optional(settings, "eyebrow");
  const heading = optional(settings, "heading");
  const body = optional(settings, "body");
  const image = optional(settings, "image");
  const tint = optional(settings, "tint");
  const ctaLabel = optional(settings, "ctaLabel");
  const bleeding = bool(settings, BLEED_SETTING, false);

  const split = width >= SPLIT_WIDTH;

  const tiles = TILES.map((slot) => ({
    slot,
    title: str(settings, `tile${slot}Title`).trim(),
    image: optional(settings, `tile${slot}Image`),
    href: optional(settings, `tile${slot}Href`),
  })).filter((tile) => tile.title !== "");

  return (
    <section
      style={{
        background: tint ?? T.surface,
        color: T.text,
        border: `1px solid ${T.border}`,
        ...(bleeding ? {} : { borderRadius: T.radius }),
        boxSizing: "border-box",
        height: "100%",
        paddingBlock: step(split ? 2 : 1.4),
        paddingInline: bleeding ? pageGutter(width < STACK_WIDTH) : step(split ? 2 : 1.2),
      }}
    >
      <div
        style={{
          width: "100%",
          maxWidth: T.maxWidth,
          marginInline: "auto",
          display: "grid",
          gridTemplateColumns: split ? "minmax(0, 1.25fr) minmax(0, 0.75fr)" : "minmax(0, 1fr)",
          gap: step(split ? 2 : 1.1),
          alignItems: "center",
        }}
      >
        <div style={{ display: "flex", flexDirection: "column", gap: step(0.7), minWidth: 0 }}>
          {eyebrow ? (
            <span style={{ color: T.text, opacity: 0.72 }}>
              <Eyebrow tone="inherit">{eyebrow}</Eyebrow>
            </span>
          ) : null}
          {heading ? (
            <StoreHeading level={2} size={split ? "lg" : "md"} width={width}>
              {heading}
            </StoreHeading>
          ) : null}
          {body ? <StoreText style={{ opacity: 0.82 }}>{body}</StoreText> : null}

          {tiles.length > 0 ? (
            <ul
              style={{
                listStyle: "none",
                margin: 0,
                padding: 0,
                marginTop: step(0.3),
                display: "grid",
                gridTemplateColumns: `repeat(${Math.min(TILES_ACROSS, tiles.length)}, minmax(0, 1fr))`,
                gap: step(0.6),
                maxWidth: step(TILE_ROW_MAX),
              }}
            >
              {tiles.map((tile) => (
                <li key={tile.slot} style={{ minWidth: 0 }}>
                  <TileShell
                    href={tile.href}
                    style={{
                      display: "flex",
                      flexDirection: "column",
                      alignItems: "center",
                      gap: step(0.35),
                      height: "100%",
                      background: T.surface,
                      border: `1px solid ${T.border}`,
                      borderRadius: T.radius,
                      padding: step(0.5),
                      textDecoration: "none",
                    }}
                  >
                    <ImagePlate
                      seed={`promo-${tile.slot}-${tile.title}`}
                      src={tile.image}
                      style={{
                        aspectRatio: "1 / 1",
                        minHeight: 0,
                        border: "none",
                        borderRadius: `calc(${T.radius} / 2)`,
                        objectFit: "contain",
                      }}
                    />
                    <span
                      style={{
                        fontFamily: T.fontBody,
                        fontSize: "0.75rem",
                        fontWeight: 700,
                        lineHeight: 1.25,
                        color: T.text,
                        textAlign: "center",
                        textWrap: "balance",
                      }}
                    >
                      {tile.title}
                    </span>
                  </TileShell>
                </li>
              ))}
            </ul>
          ) : null}

          {/*
            The one thing on the tint that does *not* take `T.text`, and deliberately.

            A solid action carries its own ground with it — `T.accent` behind `T.accentText` — and
            that pair is guaranteed by the theme wherever it lands, including on a colour the
            merchant typed. `tone="inherit"` exists for the opposite case, an *outline* action
            whose words and border would otherwise be drawn from the page's pair; it does nothing
            to a solid one, by design. So this stays as it is: the eyebrow above inherits because
            it is bare text on the tint, and the button does not because it is not.

            The button being the page's only accent-coloured shape is also what makes it read as
            the thing to press, which is the entire job of a promo banner.
          */}
          {ctaLabel ? (
            <div style={{ marginTop: step(0.5) }}>
              <ActionLink href={str(settings, "ctaHref", "/store/catalogue")} size="sm">
                {ctaLabel}
              </ActionLink>
            </div>
          ) : null}
        </div>

        {split ? (
          <ImagePlate
            seed={`promo-${heading ?? "banner"}`}
            src={image}
            style={{ aspectRatio: "4 / 3", minHeight: 0, border: "none", objectFit: "contain" }}
          />
        ) : null}
      </div>
    </section>
  );
}
