import { resolveImageSrc } from "./assetSrc";
import { ImagePlate, SectionEmpty, StoreHeading } from "./primitives";
import { choice, clampNum, optional } from "./settings";
import { T, step } from "./tokens";
import type { SectionRenderProps } from "./types";

/**
 * Twelve slots, not eight.
 *
 * Twelve is the count that divides evenly by every column count this section offers — 2, 3 and 4 —
 * so a merchant who fills the block never ends on a row of one. Eight does not divide by three,
 * and a Gallery or an Infrastructure page is exactly the page that runs past eight pictures. The
 * price of twelve is paid in editor controls, so a slot here is two fields and not four.
 */
const SLOTS = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12] as const;

const ASPECTS = ["wide", "landscape", "square", "tall"] as const;

const ASPECT_RATIO: Record<(typeof ASPECTS)[number], string> = {
  wide: "3 / 2",
  landscape: "4 / 3",
  square: "1 / 1",
  tall: "3 / 4",
};

/**
 * A grid of pictures with captions: Gallery, Infrastructure, Projects, Quality & Manufacturing.
 *
 * **The caption is also the alternative text.** A picture whose caption is visible beside it does
 * not want the same words announced twice, but a picture with *no* caption and no alt is a silent
 * hole in the page — so a captioned picture is a `<figure>` with a `<figcaption>` and an empty alt,
 * and an uncaptioned one is decoration. That is the honest reading of both cases and it needs no
 * ninth field.
 *
 * **No lightbox.** A dialog is focus trapping, an escape route, a restore point and a keyboard
 * contract — real interaction that this pack cannot test properly and that a merchant's gallery
 * does not need to be useful.
 */
export function MediaGallery({ settings, stacked, width }: SectionRenderProps) {
  const heading = optional(settings, "heading");
  const columns = clampNum(settings, "columns", 3, 2, 4);
  const mobileColumns = clampNum(settings, "mobileColumns", 2, 1, 3);
  const aspect = choice(settings, "aspect", ASPECTS, "landscape");

  const items = SLOTS.map((slot) => ({
    slot,
    image: optional(settings, `item${slot}Image`),
    caption: optional(settings, `item${slot}Caption`),
  })).filter((item) => item.image !== null || item.caption !== null);

  const cols = stacked ? mobileColumns : columns;

  return (
    <section style={{ display: "flex", flexDirection: "column", gap: step(1.1) }}>
      {heading ? <StoreHeading width={width}>{heading}</StoreHeading> : null}
      {items.length === 0 ? (
        <SectionEmpty
          title="No pictures yet"
          hint="Upload a picture and give it a caption in this section's settings to show it here."
        />
      ) : (
        <ul
          style={{
            listStyle: "none",
            margin: 0,
            padding: 0,
            display: "grid",
            gridTemplateColumns: `repeat(${Math.min(cols, items.length)}, minmax(0, 1fr))`,
            gap: step(0.9),
          }}
        >
          {items.map((item) => (
            <li key={item.slot}>
              <figure style={{ margin: 0, display: "flex", flexDirection: "column", gap: step(0.45) }}>
                <ImagePlate
                  seed={`gallery-${item.slot}`}
                  src={resolveImageSrc(item.image)}
                  /* Empty alt on purpose where a caption is present: the caption is already in the
                     document and beside the picture, so an alt repeating it makes a screen reader
                     read the same sentence twice. Where there is no caption there is nothing to
                     announce and the picture is decoration. */
                  alt=""
                  style={{ aspectRatio: ASPECT_RATIO[aspect], minHeight: 0 }}
                />
                {item.caption ? (
                  <figcaption
                    style={{
                      fontFamily: T.fontBody,
                      fontSize: "0.8125rem",
                      lineHeight: 1.45,
                      color: T.muted,
                    }}
                  >
                    {item.caption}
                  </figcaption>
                ) : null}
              </figure>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
