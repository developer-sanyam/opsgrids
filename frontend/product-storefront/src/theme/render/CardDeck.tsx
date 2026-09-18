import { isSafeHref } from "./linkList";
import { ImagePlate, SectionEmpty, StoreHeading, StoreText } from "./primitives";
import { choice, clampNum, optional, str } from "./settings";
import { TileShell } from "./TileShell";
import { T, step } from "./tokens";
import type { SectionRenderProps } from "./types";

/** Six slots: two full rows at three across, three at two across. */
const SLOTS = [1, 2, 3, 4, 5, 6] as const;

/** `none` is a real answer — a Careers page is a list of roles and has no photography. */
const ASPECTS = ["none", "wide", "landscape", "square", "tall"] as const;

const ASPECT_RATIO: Record<Exclude<(typeof ASPECTS)[number], "none">, string> = {
  wide: "3 / 2",
  landscape: "4 / 3",
  square: "1 / 1",
  tall: "3 / 4",
};

/**
 * Anything article-shaped: Case Studies, Projects, Careers, Press, Brands.
 *
 * **Why there is no jobs section.** A role is a title, a place, a contract type and a paragraph,
 * which is this card with its detail line reading "Nashik · Full time" instead of "Tata Projects ·
 * 2025". A separate section would differ by two words of default copy and would then have to be
 * kept in step with this one for every layout change. One generic `meta` line covers it, and the
 * merchant writes the words.
 *
 * **The whole card is the link**, not a "read more" inside it. A card with a link inside it gives a
 * mouse user a large target that does nothing and a keyboard user a tiny one; the visible link
 * wording stays in the card as an affordance, inside the same control, so the accessible name is
 * the title and the summary and the wording — which is what a screen-reader user needs to decide
 * whether to follow it. A card with no link renders a `<div>`, because a control that looks
 * tappable and swallows the tap is worse than a picture.
 */
export function CardDeck({ settings, stacked, width }: SectionRenderProps) {
  const heading = optional(settings, "heading");
  const columns = clampNum(settings, "columns", 3, 2, 4);
  const mobileColumns = clampNum(settings, "mobileColumns", 1, 1, 2);
  const aspect = choice(settings, "aspect", ASPECTS, "wide");
  const linkLabel = str(settings, "linkLabel", "").trim();

  const cards = SLOTS.map((slot) => ({
    slot,
    image: optional(settings, `item${slot}Image`),
    title: optional(settings, `item${slot}Title`),
    meta: optional(settings, `item${slot}Meta`),
    body: optional(settings, `item${slot}Body`),
    href: optional(settings, `item${slot}Href`),
  })).filter((card) => card.title !== null);

  const cols = stacked ? mobileColumns : columns;

  return (
    <section style={{ display: "flex", flexDirection: "column", gap: step(1.1) }}>
      {heading ? <StoreHeading width={width}>{heading}</StoreHeading> : null}
      {cards.length === 0 ? (
        <SectionEmpty
          title="No cards yet"
          hint="Give a card a title in this section's settings. A card with no title is not shown."
        />
      ) : (
        <ul
          style={{
            listStyle: "none",
            margin: 0,
            padding: 0,
            display: "grid",
            gridTemplateColumns: `repeat(${Math.min(cols, cards.length)}, minmax(0, 1fr))`,
            gap: step(0.9),
            alignItems: "stretch",
          }}
        >
          {cards.map((card) => (
            <li key={card.slot} style={{ display: "flex" }}>
              <TileShell
                href={card.href}
                style={{
                  display: "flex",
                  flexDirection: "column",
                  width: "100%",
                  background: T.surface,
                  border: `1px solid ${T.border}`,
                  borderRadius: T.radius,
                  overflow: "hidden",
                  textDecoration: "none",
                  color: T.text,
                }}
              >
                {aspect === "none" ? null : (
                  <ImagePlate
                    seed={`card-${card.slot}`}
                    src={card.image}
                    alt=""
                    style={{
                      aspectRatio: ASPECT_RATIO[aspect],
                      minHeight: 0,
                      border: "none",
                      borderRadius: 0,
                    }}
                  />
                )}
                {/* A `<div>` and not a `<span>`: the card's body holds a heading and a
                    paragraph, which are flow content, and phrasing content may not. An `<a>` is
                    allowed to wrap flow content, so the link still covers the whole card. */}
                <div
                  style={{
                    display: "flex",
                    flexDirection: "column",
                    gap: step(0.35),
                    padding: step(1),
                    flex: "1 1 auto",
                  }}
                >
                  {card.meta ? (
                    <span
                      style={{
                        fontFamily: T.fontBody,
                        fontSize: "0.75rem",
                        fontWeight: 700,
                        letterSpacing: "0.06em",
                        textTransform: "uppercase",
                        color: T.muted,
                      }}
                    >
                      {card.meta}
                    </span>
                  ) : null}
                  <StoreHeading level={3} size="md" style={{ overflowWrap: "anywhere" }}>
                    {card.title}
                  </StoreHeading>
                  {card.body ? (
                    <StoreText muted small style={{ maxWidth: "none" }}>
                      {card.body}
                    </StoreText>
                  ) : null}
                  {/* Pushed to the foot, so cards of different lengths still line their wording up
                      with each other rather than floating mid-card. */}
                  {/* `isSafeHref`, not a null check: a refused destination (a `javascript:` a
                      merchant pasted) is non-null, and the tile has already dropped to a plain
                      block. Showing accent-coloured "read more" wording over a card that cannot
                      be followed is link-looking text with no link behind it. */}
                  {isSafeHref(card.href ?? "") && linkLabel !== "" ? (
                    <span
                      style={{
                        marginTop: "auto",
                        paddingTop: step(0.5),
                        fontFamily: T.fontBody,
                        fontSize: "0.8125rem",
                        fontWeight: 700,
                        color: T.accent,
                      }}
                    >
                      {linkLabel}
                    </span>
                  ) : null}
                </div>
              </TileShell>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
