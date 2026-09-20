import type { CSSProperties } from "react";

import { BLEED_SETTING } from "../schema";
import { resolveImageSrc } from "./assetSrc";
import { BAND_LAYOUTS, BAND_OVERLAYS, bandGround, bandInline, overlayLayer } from "./band";
import { ActionLink, ImagePlate, StoreHeading, StoreText } from "./primitives";
import { bool, choice, optional, str } from "./settings";
import { T, step } from "./tokens";
import type { SectionRenderProps } from "./types";

const SIDES = ["left", "right"] as const;

/**
 * A plate beside a block of copy — or, in `overlay`, the copy set on top of the plate.
 *
 * `overlay` is the editorial band: a photograph running the width of the page with a paragraph
 * over it. It lives here rather than in a section of its own because it is the same three pieces
 * of content — a picture, a heading, a paragraph — arranged differently, and a near-identical twin
 * section is a second place for a merchant's copy to be typed and then drift.
 *
 * `split` stacks plate-first below the breakpoint and is untouched.
 */
export function ImageWithText({ settings, stacked, width }: SectionRenderProps) {
  const side = choice(settings, "imageSide", SIDES, "left");
  const layout = choice(settings, "layout", BAND_LAYOUTS, "split");
  const heading = optional(settings, "heading");
  const body = optional(settings, "body");
  const caption = optional(settings, "caption");
  const ctaLabel = optional(settings, "ctaLabel");
  const image = optional(settings, "image");
  const tint = optional(settings, "tint");
  const bleed = bool(settings, BLEED_SETTING, false);

  if (layout === "overlay") {
    const overlay = choice(settings, "overlay", BAND_OVERLAYS, "scrim");
    /* With no tint the band borrows the `contrast` ground, which is the one pair guaranteed to be
       legible over a photograph in every palette. */
    const { background, foreground } = bandGround("contrast", tint);
    const wash = overlayLayer(overlay, background);

    return (
      <section
        style={{
          position: "relative",
          overflow: "hidden",
          isolation: "isolate",
          borderRadius: bleed ? 0 : T.radius,
          minHeight: step(14),
          height: "100%",
          display: "grid",
          alignItems: "center",
        }}
      >
        <ImagePlate
          seed={`iwt-${heading ?? "plate"}`}
          src={resolveImageSrc(image)}
          style={{
            position: "absolute",
            inset: 0,
            width: "100%",
            height: "100%",
            minHeight: 0,
            border: "none",
            borderRadius: 0,
          }}
        />
        {wash ? <span aria-hidden style={{ position: "absolute", inset: 0, ...wash }} /> : null}
        <div
          style={{
            position: "relative",
            width: "100%",
            paddingBlock: step(stacked ? 2 : 3),
            paddingInline: bandInline(bleed, stacked, step(stacked ? 1.5 : 2.5)),
          }}
        >
          <div style={{ display: "flex", flexDirection: "column", gap: step(0.75), maxWidth: "min(100%, 40rem)" }}>
            {heading ? <StoreHeading width={width} style={{ color: foreground }}>{heading}</StoreHeading> : null}
            {body ? <StoreText style={{ color: foreground, opacity: 0.88 }}>{body}</StoreText> : null}
            {caption ? (
              <span style={{ fontFamily: T.fontBody, fontSize: "0.75rem", color: foreground, opacity: 0.72 }}>
                {caption}
              </span>
            ) : null}
            {ctaLabel ? (
              <div style={{ marginTop: step(0.3) }}>
                {/* `outline` takes its colour from the theme's text, which is the wrong end of the
                    pair on a scrim — the band's own foreground is applied over it. */}
                <span style={{ color: foreground }}>
                  <ActionLink href={str(settings, "ctaHref", "/store/catalogue")} variant="outline">
                    <span style={{ color: foreground }}>{ctaLabel}</span>
                  </ActionLink>
                </span>
              </div>
            ) : null}
          </div>
        </div>
      </section>
    );
  }

  const plate = (
    <figure style={{ margin: 0, display: "flex", flexDirection: "column", gap: step(0.4) }}>
      <ImagePlate seed={`iwt-${heading ?? "plate"}`} src={resolveImageSrc(image)} style={{ minHeight: step(10) }} />
      {caption ? (
        <figcaption style={{ fontFamily: T.fontBody, fontSize: "0.75rem", color: T.muted }}>
          {caption}
        </figcaption>
      ) : null}
    </figure>
  );

  const copy = (
    <div style={{ display: "flex", flexDirection: "column", gap: step(0.75), justifyContent: "center" }}>
      {heading ? <StoreHeading width={width}>{heading}</StoreHeading> : null}
      {body ? <StoreText muted>{body}</StoreText> : null}
      {ctaLabel ? (
        <div style={{ marginTop: step(0.3) }}>
          <ActionLink href={str(settings, "ctaHref", "/store/catalogue")} variant="outline">
            {ctaLabel}
          </ActionLink>
        </div>
      ) : null}
    </div>
  );

  /* No tint and no bleed is the arrangement the trade themes ship: a bare grid with no ground and
     no padding of its own. Either one turns the section into a band, which needs both. */
  const groundPad = tint !== null ? step(stacked ? 1.25 : 2) : "0px";
  const frame: CSSProperties =
    tint !== null || bleed
      ? {
          ...(tint !== null ? { background: tint, borderRadius: bleed ? 0 : T.radius } : {}),
          paddingBlock: groundPad,
          paddingInline: bandInline(bleed, stacked, groundPad),
        }
      : {};

  return (
    <section
      style={{
        display: "grid",
        gridTemplateColumns: stacked ? "minmax(0, 1fr)" : "minmax(0, 1fr) minmax(0, 1fr)",
        gap: step(1.75),
        alignItems: "center",
        height: "100%",
        ...frame,
      }}
    >
      {side === "left" || stacked ? (
        <>
          {plate}
          {copy}
        </>
      ) : (
        <>
          {copy}
          {plate}
        </>
      )}
    </section>
  );
}
