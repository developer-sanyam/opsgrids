import { ActionLink, Eyebrow, ImagePlate, StoreText } from "./primitives";
import { choice, optional, str } from "./settings";
import { T, step } from "./tokens";
import type { SectionRenderProps } from "./types";

const TONES = ["surface", "accent", "contrast"] as const;
const ALIGNS = ["left", "center"] as const;

/** The page opener: eyebrow, heading, a sentence, up to two actions, and an optional plate. */
export function Hero({ settings, stacked }: SectionRenderProps) {
  const tone = choice(settings, "tone", TONES, "contrast");
  const align = choice(settings, "align", ALIGNS, "left");
  const heading = str(settings, "heading", "Heading");
  const eyebrow = optional(settings, "eyebrow");
  const body = optional(settings, "body");
  const ctaLabel = optional(settings, "ctaLabel");
  const secondaryLabel = optional(settings, "secondaryLabel");
  const image = optional(settings, "image");
  const centred = align === "center";

  const background = tone === "accent" ? T.accent : tone === "contrast" ? T.text : T.surface;
  const foreground = tone === "accent" ? T.accentText : tone === "contrast" ? T.bg : T.text;
  const showPlate = !centred && !stacked;

  return (
    <section
      style={{
        background,
        color: foreground,
        border: tone === "surface" ? `1px solid ${T.border}` : "none",
        borderRadius: T.radius,
        padding: step(stacked ? 1.5 : 2.5),
        height: "100%",
        display: "grid",
        gridTemplateColumns: showPlate ? "minmax(0, 1.1fr) minmax(0, 0.9fr)" : "minmax(0, 1fr)",
        gap: step(2),
        alignItems: "center",
      }}
    >
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
            fontSize: "clamp(2rem, 4.4vw, 3.4rem)",
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
            {secondaryLabel ? (
              <span style={{ color: foreground }}>
                <ActionLink href={str(settings, "secondaryHref", "/store/catalogue")} variant="outline">
                  <span style={{ color: foreground }}>{secondaryLabel}</span>
                </ActionLink>
              </span>
            ) : null}
          </div>
        ) : null}
      </div>
      {showPlate ? (
        <ImagePlate seed={`hero-${heading}`} src={image} style={{ minHeight: step(11) }} />
      ) : null}
    </section>
  );
}
