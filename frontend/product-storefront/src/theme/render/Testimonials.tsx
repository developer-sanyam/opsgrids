import { SectionEmpty, StoreHeading, StoreText } from "./primitives";
import { optional } from "./settings";
import { T, step } from "./tokens";
import type { SectionRenderProps } from "./types";

const SLOTS = [1, 2, 3] as const;

/** Up to three quotes. A quote with no text is skipped; an author with no quote is not a quote. */
export function Testimonials({ settings, stacked, width }: SectionRenderProps) {
  const heading = optional(settings, "heading");
  const quotes = SLOTS.map((slot) => ({
    slot,
    quote: optional(settings, `quote${slot}`),
    author: optional(settings, `author${slot}`),
    role: optional(settings, `role${slot}`),
  })).filter((entry) => entry.quote !== null);

  return (
    <section style={{ display: "flex", flexDirection: "column", gap: step(1.1), height: "100%" }}>
      {heading ? <StoreHeading width={width}>{heading}</StoreHeading> : null}
      {quotes.length === 0 ? (
        <SectionEmpty title="No quotes yet" hint="Add a quote in this section's settings to show it here." />
      ) : (
        <div
          style={{
            display: "grid",
            gridTemplateColumns: stacked ? "minmax(0, 1fr)" : `repeat(${quotes.length}, minmax(0, 1fr))`,
            gap: step(0.9),
          }}
        >
          {quotes.map((entry) => (
            <figure
              key={entry.slot}
              style={{
                margin: 0,
                display: "flex",
                flexDirection: "column",
                gap: step(0.6),
                background: T.surface,
                borderLeft: `3px solid ${T.accent}`,
                borderRadius: T.radius,
                padding: step(1.1),
              }}
            >
              <blockquote style={{ margin: 0 }}>
                <StoreText style={{ fontSize: "1.0625rem", lineHeight: 1.5 }}>{entry.quote}</StoreText>
              </blockquote>
              {entry.author || entry.role ? (
                <figcaption
                  style={{ fontFamily: T.fontBody, fontSize: "0.8125rem", color: T.muted, lineHeight: 1.4 }}
                >
                  {entry.author ? <strong style={{ color: T.text }}>{entry.author}</strong> : null}
                  {entry.author && entry.role ? <br /> : null}
                  {entry.role}
                </figcaption>
              ) : null}
            </figure>
          ))}
        </div>
      )}
    </section>
  );
}
