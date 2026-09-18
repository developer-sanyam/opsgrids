import { SectionEmpty, StoreHeading, StoreText } from "./primitives";
import { bool, optional } from "./settings";
import { T, step } from "./tokens";
import type { SectionRenderProps } from "./types";

const SLOTS = [1, 2, 3, 4] as const;

/**
 * Questions and answers as native `<details>`.
 *
 * Native because it is keyboard-complete, announces its expanded state, and is findable by the
 * browser's own in-page search even while collapsed — three things a hand-rolled accordion has to
 * re-earn and usually does not.
 */
export function Faq({ settings }: SectionRenderProps) {
  const heading = optional(settings, "heading");
  const openFirst = bool(settings, "openFirst", true);
  const entries = SLOTS.map((slot) => ({
    slot,
    question: optional(settings, `q${slot}`),
    answer: optional(settings, `a${slot}`),
  })).filter((entry) => entry.question !== null);

  return (
    <section style={{ display: "flex", flexDirection: "column", gap: step(1), height: "100%" }}>
      {heading ? <StoreHeading>{heading}</StoreHeading> : null}
      {entries.length === 0 ? (
        <SectionEmpty title="No questions yet" hint="Add a question and its answer in this section's settings." />
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: step(0.4) }}>
          {entries.map((entry, index) => (
            <details
              key={entry.slot}
              open={openFirst && index === 0}
              style={{
                background: T.surface,
                border: `1px solid ${T.border}`,
                borderRadius: T.radius,
                padding: `${step(0.7)} ${step(0.9)}`,
              }}
            >
              <summary
                style={{
                  fontFamily: T.fontHeading,
                  fontWeight: 700,
                  fontSize: "0.9375rem",
                  color: T.text,
                  cursor: "pointer",
                  listStyle: "revert",
                }}
              >
                {entry.question}
              </summary>
              {entry.answer ? (
                <StoreText muted small style={{ marginTop: step(0.4) }}>
                  {entry.answer}
                </StoreText>
              ) : null}
            </details>
          ))}
        </div>
      )}
    </section>
  );
}
