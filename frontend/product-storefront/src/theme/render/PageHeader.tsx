import { BLEED_SETTING } from "../schema";
import { BAND_TONES, bandGround, bandInline } from "./band";
import { Eyebrow, SectionEmpty, StoreText } from "./primitives";
import { bool, choice, lines, optional } from "./settings";
import { T, step } from "./tokens";
import type { SectionRenderProps } from "./types";

const RANKS = ["page", "section"] as const;
const ALIGNS = ["left", "center"] as const;

/**
 * The title's fluid step, measured against the **canvas** and never the viewport.
 *
 * These three numbers are the `xl` step of the heading ramp in `primitives.tsx`, repeated here
 * rather than imported, because `StoreHeading` types `level` as `2 | 3 | 4` — deliberately, since
 * nothing in the library below a hero should be able to claim the page's own rank — and this
 * section is the one block that sometimes *is* the page's own title. Widening `HeadingLevel` is a
 * change to a primitive, which is not this unit's file to edit.
 *
 * The drift that copying invites is pinned by a test instead: `cmsSections.test.tsx` asserts this
 * title is set at exactly the size `rich-text` gets from the shared `xl` step at the same width, so
 * moving the ramp in `primitives.tsx` without moving it here fails the suite rather than quietly
 * leaving one heading behind.
 */
const TITLE_RAMP = { floor: "2rem", slope: 0.042, ceiling: "3.1rem" } as const;

function titleFontSize(width: number): string {
  const fluid = Math.max(0, Math.round(width * TITLE_RAMP.slope * 10) / 10);
  return `clamp(${TITLE_RAMP.floor}, ${fluid}px, ${TITLE_RAMP.ceiling})`;
}

/**
 * The interior page's title block: an eyebrow, a title, a standfirst, and optionally a ground.
 *
 * Every one of the thirty-five company, support and legal pages opens with this, which is why it is
 * the first block of the pack. `hero` cannot do the job: it is a band with a plate, two calls to
 * action, an overlay and a minimum height — a front page. Dropping one at the top of a Returns
 * Policy gives a merchant six controls they have to empty out and a 420px photograph they did not
 * ask for.
 *
 * **The heading rank, which is the real decision here.**
 *
 * An interior page's title *is* its `h1` — it is the first and most important thing on the page,
 * and a page whose only `h1` is the store's logo has no document outline at all. So the default is
 * `h1`, and the consequence is stated plainly in the field's help: this section and `hero` must
 * never both sit on one page.
 *
 * It cannot be enforced from here. A section renders from `{settings, products, categories,
 * stacked, width, mode}` and has no idea what else is on the page — by design, because that is what
 * makes the same component work in the buyer's view, the gallery's miniature and the editor's
 * canvas. So the *merchant* gets the choice, as a two-value select with a default that is right for
 * the pages this block exists for, and the runtime keeps rendering exactly one `h1` per page as
 * long as they do not place both. A page-level check belongs in the editor's own validation, which
 * is another unit's file; this is noted in the handover rather than smuggled in here.
 *
 * The existing "exactly one `h1` per page" sweep is unaffected: it renders the themes' own pages,
 * none of which carry this section, and every one of which still gets its single `h1` from `hero`.
 *
 * **No `height: 100%`, unlike the older sections.** Found in Chrome, invisible in jsdom: a
 * percentage height against a grid area whose own height is still being decided contributes nothing
 * to the row sizing, so a block whose content runs past the rows it was dropped into keeps its
 * short box *and overflows it* — and a section that paints a ground then paints that ground over
 * the top of the section below. On the About page it put the picture gallery's heading in black
 * text under a black band. Content-sized instead: the ground is exactly as tall as the words and
 * their padding, which is what a title block wants anyway, and it can never draw outside its own
 * box. The other six sections in this pack are content-sized for the same reason.
 */
export function PageHeader({ settings, stacked, width }: SectionRenderProps) {
  const rank = choice(settings, "rank", RANKS, "page");
  const align = choice(settings, "align", ALIGNS, "left");
  const tone = choice(settings, "tone", BAND_TONES, "surface");
  const tint = optional(settings, "tint");
  const bleed = bool(settings, BLEED_SETTING, false);
  const eyebrow = optional(settings, "eyebrow");
  const heading = optional(settings, "heading");
  const standfirst = lines(settings, "standfirst");

  const { background, foreground, bordered } = bandGround(tone, tint);
  const grounded = tone !== "none" || tint !== null;
  const centred = align === "center";
  const pad = step(stacked ? 1.4 : 2.2);

  /* On an accent or contrast ground the theme's accent is not guaranteed to be readable, so the
     eyebrow takes the foreground that ground was paired with. A merchant's tint is a brand
     *surface*, and `bandGround` already puts the theme's own text on it, so the accent stays. */
  const eyebrowTone = tint === null && (tone === "accent" || tone === "contrast") ? "inherit" : "accent";

  const Title = rank === "page" ? "h1" : "h2";

  return (
    <section
      style={{
        display: "flex",
        flexDirection: "column",
        gap: step(0.6),
        alignItems: centred ? "center" : "flex-start",
        textAlign: centred ? "center" : "left",
        justifyContent: "center",
        ...(grounded
          ? {
              background,
              color: foreground,
              border: bordered ? `1px solid ${T.border}` : "none",
              borderRadius: bleed ? 0 : T.radius,
            }
          : {}),
        /* A bleeding header spans the canvas rails, so its own padding is no longer what a reader
           measures from the edge — without this the title would start at the window edge while
           every section below it started at the content column. */
        ...(grounded || bleed
          ? { paddingBlock: pad, paddingInline: bandInline(bleed, stacked, pad) }
          : {}),
      }}
    >
      {heading === null ? (
        <SectionEmpty
          title="No page title yet"
          hint="Type the page's title in this section's settings — About us, Shipping policy, Contact."
        />
      ) : (
        <>
          {eyebrow ? (
            <span style={{ color: foreground, opacity: eyebrowTone === "inherit" ? 0.88 : 1 }}>
              <Eyebrow tone={eyebrowTone}>{eyebrow}</Eyebrow>
            </span>
          ) : null}
          <Title
            style={{
              fontFamily: T.fontHeading,
              fontSize: titleFontSize(width),
              fontWeight: 700,
              lineHeight: 1.12,
              letterSpacing: "-0.02em",
              textWrap: "balance",
              color: foreground,
              margin: 0,
            }}
          >
            {heading}
          </Title>
          {standfirst.map((paragraph) => (
            <StoreText
              key={paragraph.slice(0, 40)}
              muted={!grounded}
              style={{
                fontSize: "1.05rem",
                ...(grounded ? { color: foreground, opacity: 0.88 } : {}),
                ...(centred ? { marginInline: "auto" } : {}),
              }}
            >
              {paragraph}
            </StoreText>
          ))}
        </>
      )}
    </section>
  );
}
