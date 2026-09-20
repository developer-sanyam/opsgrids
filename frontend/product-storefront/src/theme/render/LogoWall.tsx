import { SectionEmpty, StoreHeading } from "./primitives";
import { bool, lines, optional } from "./settings";
import { T, step } from "./tokens";
import type { SectionRenderProps } from "./types";

/**
 * Customer names as wordmarks in the theme's heading face.
 *
 * Text, not uploaded logos: a merchant does not have permission to a customer's mark in most
 * cases, and a wall of mismatched PNGs is the fastest way to make a real store look fake.
 */
export function LogoWall({ settings }: SectionRenderProps) {
  const heading = optional(settings, "heading");
  const names = lines(settings, "names");
  const muted = bool(settings, "muted", true);

  return (
    <section
      style={{
        display: "flex",
        flexDirection: "column",
        gap: step(0.9),
        height: "100%",
        justifyContent: "center",
      }}
    >
      {heading ? (
        <StoreHeading level={2} size="sm" style={{ color: T.muted, letterSpacing: "0.1em", textTransform: "uppercase" }}>
          {heading}
        </StoreHeading>
      ) : null}
      {names.length === 0 ? (
        <SectionEmpty title="No names yet" hint="Add one company name per line in this section's settings." />
      ) : (
        <ul
          style={{
            listStyle: "none",
            margin: 0,
            padding: 0,
            display: "flex",
            flexWrap: "wrap",
            gap: `${step(0.6)} ${step(1.6)}`,
            alignItems: "center",
          }}
        >
          {names.map((name) => (
            <li
              key={name}
              style={{
                fontFamily: T.fontHeading,
                fontSize: "1.0625rem",
                fontWeight: 700,
                letterSpacing: "-0.01em",
                color: muted ? T.muted : T.text,
              }}
            >
              {name}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
