import { BLEED_SETTING } from "../schema";
import { resolveImageSrc } from "./assetSrc";
import {
  BAND_LAYOUTS,
  BAND_OVERLAYS,
  IMAGE_FOCUS,
  bandGround,
  bandInline,
  overlayLayer,
} from "./band";
import { ActionLink, Eyebrow, ImagePlate, StoreText, type ActionTone } from "./primitives";
import { bool, choice, clampNum, optional, str } from "./settings";
import { T, step } from "./tokens";
import type { SectionRenderProps } from "./types";

const TONES = ["surface", "accent", "contrast"] as const;
const ALIGNS = ["left", "center"] as const;

/** Bounds on the merchant's overlay height, in px. Validation of typed data, not a design value. */
const MIN_HEIGHT = { fallback: 420, floor: 240, ceiling: 720 } as const;

/**
 * The heading's fluid ramp, measured against the **canvas** rather than the viewport.
 *
 * It used to be `clamp(2rem, 4.4vw, 3.4rem)`. `vw` is the browser window, but a storefront canvas
 * is a fixed-width box — 390px in the editor's phone preview, a scaled 1280 in the gallery's
 * miniature — so the heading read at full desktop size inside a phone frame and the preview lied
 * about what a buyer would see. `width` is the canvas, which the renderers already thread.
 *
 * The three numbers are the old ones, so a real browser is unmoved: at any window where the store
 * fills the width, `canvas × 0.044` is what `4.4vw` was. Floor and ceiling stay in `rem` on
 * purpose — they must keep scaling with a reader's own default font size, which a computed px
 * value would quietly freeze.
 */
const HEADING_RAMP = { floor: "2rem", slope: 0.044, ceiling: "3.4rem" } as const;

function headingFontSize(width: number): string {
  // One decimal, so the ramp matches what `4.4vw` resolved to rather than approaching it, while
  // the declaration stays readable in devtools.
  const fluid = Math.max(0, Math.round(width * HEADING_RAMP.slope * 10) / 10);
  return `clamp(${HEADING_RAMP.floor}, ${fluid}px, ${HEADING_RAMP.ceiling})`;
}

/**
 * The page opener: eyebrow, heading, a sentence, up to two actions, and a picture.
 *
 * Two arrangements, one set of words. **Split** puts the plate beside the copy and is what every
 * trade theme ships — it is the default, and nothing about it moved. **Overlay** puts the copy on
 * top of a photograph that fills the section, which is the shape of a furniture showroom's front
 * page and is only readable because of the scrim between them.
 *
 * The copy is built once and placed by whichever arrangement is chosen, so the two can never drift
 * into offering different words or a different heading rank. There is exactly one `h1` either way.
 */
export function Hero({ settings, stacked, width }: SectionRenderProps) {
  const tone = choice(settings, "tone", TONES, "contrast");
  const align = choice(settings, "align", ALIGNS, "left");
  const layout = choice(settings, "layout", BAND_LAYOUTS, "split");
  const heading = str(settings, "heading", "Heading");
  const eyebrow = optional(settings, "eyebrow");
  const body = optional(settings, "body");
  const ctaLabel = optional(settings, "ctaLabel");
  const secondaryLabel = optional(settings, "secondaryLabel");
  const image = optional(settings, "image");
  const bleed = bool(settings, BLEED_SETTING, false);
  const centred = align === "center";

  const { background, foreground } = bandGround(tone);
  const showPlate = !centred && !stacked;
  const pad = step(stacked ? 1.5 : 2.5);

  /**
   * Which pair the outline action takes.
   *
   * `surface` in the split arrangement is the theme's *own* ground — it is what every trade hero
   * ships on — so the action keeps the theme's pair there and nothing that already shipped moves:
   * `T.text` for the words, `T.border` for the edge, which is the pair that ground was designed
   * around.
   *
   * Everywhere else the hero paints a ground the theme's page-level pair was not chosen against:
   * an accent or contrast panel, or a scrim over a merchant's photograph. There `T.text` is the
   * wrong end of the pair — on `contrast` it is the ground's own colour, so the words vanish —
   * and `T.border` was drawn to separate a surface from the page, not to outline a control on a
   * photograph. `inherit` hands both jobs to the foreground the band has already settled, which
   * is the one colour guaranteed readable on that ground.
   *
   * The dark furniture theme is where this bites: its heroes are overlays on `surface`, whose
   * border token is a near-black brown. An outline action on that scrim had an edge nobody could
   * see.
   */
  const actionTone: ActionTone = layout === "overlay" || tone !== "surface" ? "inherit" : "theme";

  const copy = (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        gap: step(0.9),
        alignItems: centred ? "center" : "flex-start",
        textAlign: centred ? "center" : "left",
      }}
    >
      {eyebrow ? (
        <span style={{ color: foreground, opacity: tone === "surface" ? 1 : 0.88 }}>
          <Eyebrow tone={tone === "surface" ? "accent" : "inherit"}>{eyebrow}</Eyebrow>
        </span>
      ) : null}
      {/* The hero carries the page's own `h1`: it is the first heading a buyer reads. */}
      <h1
        style={{
          fontFamily: T.fontHeading,
          fontSize: headingFontSize(width),
          fontWeight: 800,
          lineHeight: 1.05,
          letterSpacing: "-0.03em",
          textWrap: "balance",
          color: foreground,
          margin: 0,
        }}
      >
        {heading}
      </h1>
      {body ? (
        <StoreText style={{ color: foreground, opacity: 0.86, fontSize: "1.05rem" }}>{body}</StoreText>
      ) : null}
      {ctaLabel || secondaryLabel ? (
        <div style={{ display: "flex", flexWrap: "wrap", gap: step(0.6), marginTop: step(0.4) }}>
          {ctaLabel ? (
            <ActionLink href={str(settings, "ctaHref", "/store/catalogue")}>{ctaLabel}</ActionLink>
          ) : null}
          {/* The wrapper is what `inherit` inherits from: the overlay arrangement paints no colour
              on any ancestor of the action, so the band's foreground has to be established here.
              There used to be a second `<span>` inside the action recolouring the words alone —
              that was the workaround, and it could only ever reach the words. The border stayed
              `T.border` and went on being invisible on a dark scrim. `tone="inherit"` moves both
              to `currentColor`, so the inner span has nothing left to do. */}
          {secondaryLabel ? (
            <span style={{ color: foreground }}>
              <ActionLink
                href={str(settings, "secondaryHref", "/store/catalogue")}
                variant="outline"
                tone={actionTone}
              >
                {secondaryLabel}
              </ActionLink>
            </span>
          ) : null}
        </div>
      ) : null}
    </div>
  );

  if (layout === "overlay") {
    const overlay = choice(settings, "overlay", BAND_OVERLAYS, "scrim");
    const focus = choice(settings, "imageFocus", IMAGE_FOCUS, "center");
    const minHeight = clampNum(
      settings,
      "minHeight",
      MIN_HEIGHT.fallback,
      MIN_HEIGHT.floor,
      MIN_HEIGHT.ceiling,
    );
    const wash = overlayLayer(overlay, background);

    return (
      <section
        style={{
          position: "relative",
          overflow: "hidden",
          /* `isolate` keeps the stacking context inside the section, so a hero cannot paint over a
             neighbouring one that happens to overlap it on the canvas. */
          isolation: "isolate",
          borderRadius: bleed ? 0 : T.radius,
          minHeight,
          height: "100%",
          display: "grid",
          /* A gradient exists to seat words at the foot of a picture; an even scrim does not, so
             it centres. */
          alignItems: overlay === "gradient" && !centred ? "end" : "center",
        }}
      >
        <ImagePlate
          seed={`hero-${heading}`}
          src={resolveImageSrc(image)}
          style={{
            position: "absolute",
            inset: 0,
            width: "100%",
            height: "100%",
            minHeight: 0,
            border: "none",
            borderRadius: 0,
            objectPosition: focus,
          }}
        />
        {wash ? <span aria-hidden style={{ position: "absolute", inset: 0, ...wash }} /> : null}
        <div
          style={{
            position: "relative",
            width: "100%",
            paddingBlock: step(stacked ? 2 : 3),
            paddingInline: bandInline(bleed, stacked, pad),
            display: "flex",
            justifyContent: centred ? "center" : "flex-start",
          }}
        >
          {/* Prose over a photograph still stops at a readable measure. */}
          <div style={{ maxWidth: "min(100%, 40rem)", width: centred ? "100%" : "auto" }}>{copy}</div>
        </div>
      </section>
    );
  }

  return (
    <section
      style={{
        background,
        color: foreground,
        border: tone === "surface" ? `1px solid ${T.border}` : "none",
        borderRadius: bleed ? 0 : T.radius,
        paddingBlock: pad,
        paddingInline: bandInline(bleed, stacked, pad),
        height: "100%",
        display: "grid",
        gridTemplateColumns: showPlate ? "minmax(0, 1.1fr) minmax(0, 0.9fr)" : "minmax(0, 1fr)",
        gap: step(2),
        alignItems: "center",
      }}
    >
      {copy}
      {showPlate ? (
        <ImagePlate seed={`hero-${heading}`} src={resolveImageSrc(image)} style={{ minHeight: step(11) }} />
      ) : null}
    </section>
  );
}
