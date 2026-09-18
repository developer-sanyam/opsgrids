import { ActionLink, ImagePlate, StoreHeading, StoreText } from "./primitives";
import { choice, optional, str } from "./settings";
import { T, step } from "./tokens";
import type { SectionRenderProps } from "./types";

const SIDES = ["left", "right"] as const;

/** A plate beside a block of copy. Stacks plate-first below the breakpoint. */
export function ImageWithText({ settings, stacked }: SectionRenderProps) {
  const side = choice(settings, "imageSide", SIDES, "left");
  const heading = optional(settings, "heading");
  const body = optional(settings, "body");
  const caption = optional(settings, "caption");
  const ctaLabel = optional(settings, "ctaLabel");

  const plate = (
    <figure style={{ margin: 0, display: "flex", flexDirection: "column", gap: step(0.4) }}>
      <ImagePlate seed={`iwt-${heading ?? "plate"}`} src={optional(settings, "image")} style={{ minHeight: step(10) }} />
      {caption ? (
        <figcaption style={{ fontFamily: T.fontBody, fontSize: "0.75rem", color: T.muted }}>
          {caption}
        </figcaption>
      ) : null}
    </figure>
  );

  const copy = (
    <div style={{ display: "flex", flexDirection: "column", gap: step(0.75), justifyContent: "center" }}>
      {heading ? <StoreHeading>{heading}</StoreHeading> : null}
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

  return (
    <section
      style={{
        display: "grid",
        gridTemplateColumns: stacked ? "minmax(0, 1fr)" : "minmax(0, 1fr) minmax(0, 1fr)",
        gap: step(1.75),
        alignItems: "center",
        height: "100%",
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
