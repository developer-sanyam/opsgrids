import { BLEED_SETTING } from "../schema";
import { BAND_TONES, bandGround, bandInline } from "./band";
import { StoreHeading, StoreText, type HeadingSize } from "./primitives";
import { bool, choice, clampNum, lines, optional } from "./settings";
import { T, step } from "./tokens";
import type { SectionRenderProps } from "./types";

const ALIGNS = ["left", "center"] as const;

/**
 * The heading scale's steps, by name. `lg` is what this section has always set at.
 *
 * `display` is the top of the scale and the reason it exists: `xl` bottoms out at 2rem, so a
 * closing wordmark on a 390px canvas came out at 32px — a section title, not a name across the
 * foot of the page. The step lives in `primitives.tsx`; this list is what lets a merchant reach
 * it, and it has to stay in step with `TEXT_SIZE_OPTIONS` in `sections.ts` or the editor offers a
 * value the renderer then declines.
 */
const SIZES = ["sm", "md", "lg", "xl", "display"] as const satisfies readonly HeadingSize[];

/** The steps at which the prose under the heading stops being body copy and becomes a stand-first. */
const LIFTED_BODY: readonly HeadingSize[] = ["xl", "display"];

/**
 * A heading and prose. Blank lines in the body become paragraphs; nothing is parsed as markup.
 *
 * `size` walks the theme's own heading scale rather than inventing sizes, and `display` is what a
 * closing wordmark block is: one short line, centred, set at the top of the scale. That is why
 * there is no separate wordmark section — a section cannot read the store's brand, so a dedicated
 * one would make the merchant retype their own name and then watch the two drift apart.
 *
 * `tone` defaults to painting no ground at all, which is what prose in the page flow wants and
 * what every existing theme gets.
 */
export function RichText({ settings, stacked, width }: SectionRenderProps) {
  const align = choice(settings, "align", ALIGNS, "left");
  const columns = clampNum(settings, "columns", 1, 1, 2);
  const size = choice(settings, "size", SIZES, "lg");
  const tone = choice(settings, "tone", BAND_TONES, "none");
  const bleed = bool(settings, BLEED_SETTING, false);
  const heading = optional(settings, "heading");
  const paragraphs = lines(settings, "body");

  const { background, foreground, bordered } = bandGround(tone);
  const grounded = tone !== "none";
  const pad = grounded ? step(stacked ? 1.5 : 2.5) : "0px";
  const centred = align === "center";

  /* Small prose sets its paragraphs small too; display prose lifts them a step so a wordmark is
     not followed by body copy three sizes below it. Everything in between is the body face. */
  const bodyStyle = LIFTED_BODY.includes(size) ? { fontSize: "1.05rem" } : undefined;

  return (
    <section
      style={{
        display: "flex",
        flexDirection: "column",
        gap: step(0.9),
        alignItems: centred ? "center" : "flex-start",
        textAlign: centred ? "center" : "left",
        height: "100%",
        justifyContent: "center",
        ...(grounded
          ? {
              background,
              color: foreground,
              border: bordered ? `1px solid ${T.border}` : "none",
              borderRadius: bleed ? 0 : T.radius,
            }
          : {}),
        ...(grounded || bleed
          ? { paddingBlock: pad, paddingInline: bandInline(bleed, stacked, pad) }
          : {}),
      }}
    >
      {heading ? (
        <StoreHeading size={size} width={width} {...(grounded ? { style: { color: foreground } } : {})}>
          {heading}
        </StoreHeading>
      ) : null}
      {/* Two columns is CSS columns, not a grid: prose should flow between them, not be split
          into two independent blocks that end at different heights. */}
      <div
        style={
          columns === 2
            ? { columnCount: 2, columnGap: step(2), width: "100%" }
            : { display: "flex", flexDirection: "column", gap: step(0.6), width: "100%" }
        }
      >
        {paragraphs.map((paragraph) => (
          <StoreText
            key={paragraph.slice(0, 40)}
            muted={!grounded}
            small={size === "sm"}
            {...(grounded || bodyStyle || columns === 2
              ? {
                  style: {
                    ...bodyStyle,
                    ...(grounded ? { color: foreground, opacity: 0.88 } : {}),
                    ...(columns === 2 ? { marginBottom: step(0.6), maxWidth: "none" } : {}),
                  },
                }
              : {})}
          >
            {paragraph}
          </StoreText>
        ))}
      </div>
    </section>
  );
}
