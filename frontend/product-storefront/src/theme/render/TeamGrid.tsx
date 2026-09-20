import { resolveImageSrc } from "./assetSrc";
import { ImagePlate, SectionEmpty, StoreHeading, StoreText } from "./primitives";
import { choice, clampNum, optional } from "./settings";
import { T, step } from "./tokens";
import type { SectionRenderProps } from "./types";

/**
 * Six slots. Six is a leadership row of three and a departmental row of six, and it divides by both
 * column counts on offer. Anything longer is a staff directory, which is records with a search box
 * rather than twenty-four more controls in a settings panel.
 */
const SLOTS = [1, 2, 3, 4, 5, 6] as const;

const SHAPES = ["square", "tall", "circle"] as const;

const SHAPE_RATIO: Record<(typeof SHAPES)[number], string> = {
  square: "1 / 1",
  tall: "3 / 4",
  circle: "1 / 1",
};

/**
 * The people behind the company: Our Team, leadership, "meet the experts".
 *
 * A person with a name is a person; a photograph with no name is not. So the name is what decides
 * whether a slot renders at all — a merchant who has uploaded three photographs and typed one name
 * gets one card, not three cards where two of them are anonymous.
 *
 * The photograph is decorative (`alt=""`): the name and the role sit next to it in the document, so
 * an alt of "Anil Deshmukh" would have a screen reader say the name twice. A `<figure>` ties the
 * two together for everyone else.
 */
export function TeamGrid({ settings, stacked, width }: SectionRenderProps) {
  const heading = optional(settings, "heading");
  const columns = clampNum(settings, "columns", 3, 2, 4);
  const mobileColumns = clampNum(settings, "mobileColumns", 2, 1, 2);
  const shape = choice(settings, "shape", SHAPES, "square");

  const people = SLOTS.map((slot) => ({
    slot,
    image: optional(settings, `item${slot}Image`),
    name: optional(settings, `item${slot}Name`),
    role: optional(settings, `item${slot}Role`),
    bio: optional(settings, `item${slot}Bio`),
  })).filter((person) => person.name !== null);

  const cols = stacked ? mobileColumns : columns;
  const round = shape === "circle";

  return (
    <section style={{ display: "flex", flexDirection: "column", gap: step(1.1) }}>
      {heading ? <StoreHeading width={width}>{heading}</StoreHeading> : null}
      {people.length === 0 ? (
        <SectionEmpty
          title="Nobody here yet"
          hint="Add a name and a role in this section's settings. A photograph on its own is not shown."
        />
      ) : (
        <ul
          style={{
            listStyle: "none",
            margin: 0,
            padding: 0,
            display: "grid",
            gridTemplateColumns: `repeat(${Math.min(cols, people.length)}, minmax(0, 1fr))`,
            gap: step(1),
          }}
        >
          {people.map((person) => (
            <li key={person.slot}>
              <figure
                style={{
                  margin: 0,
                  display: "flex",
                  flexDirection: "column",
                  gap: step(0.5),
                  alignItems: round ? "center" : "stretch",
                  textAlign: round ? "center" : "left",
                }}
              >
                <ImagePlate
                  seed={`team-${person.slot}`}
                  src={resolveImageSrc(person.image)}
                  alt=""
                  style={{
                    aspectRatio: SHAPE_RATIO[shape],
                    minHeight: 0,
                    ...(round ? { borderRadius: "50%", maxWidth: step(8) } : {}),
                  }}
                />
                <figcaption style={{ display: "flex", flexDirection: "column", gap: step(0.2) }}>
                  <StoreHeading level={3} size="sm" style={{ overflowWrap: "anywhere" }}>
                    {person.name}
                  </StoreHeading>
                  {person.role ? (
                    <span
                      style={{
                        fontFamily: T.fontBody,
                        fontSize: "0.8125rem",
                        fontWeight: 600,
                        color: T.accent,
                      }}
                    >
                      {person.role}
                    </span>
                  ) : null}
                  {person.bio ? (
                    <StoreText muted small style={{ marginTop: step(0.15) }}>
                      {person.bio}
                    </StoreText>
                  ) : null}
                </figcaption>
              </figure>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
