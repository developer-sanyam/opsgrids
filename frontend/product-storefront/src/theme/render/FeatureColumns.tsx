import { Panel, SectionEmpty, StoreHeading, StoreText } from "./primitives";
import { clampNum, optional } from "./settings";
import { T, step } from "./tokens";
import type { SectionRenderProps } from "./types";

const SLOTS = [1, 2, 3, 4] as const;

/**
 * Up to four short value propositions. Empty slots are skipped rather than rendered blank, so a
 * merchant who fills three of four gets three columns and not a hole.
 */
export function FeatureColumns({ settings, stacked }: SectionRenderProps) {
  const heading = optional(settings, "heading");
  const columns = clampNum(settings, "columns", 3, 2, 4);

  const items = SLOTS.map((slot) => ({
    slot,
    title: optional(settings, `item${slot}Title`),
    body: optional(settings, `item${slot}Body`),
  })).filter((item) => item.title !== null || item.body !== null);

  return (
    <section style={{ display: "flex", flexDirection: "column", gap: step(1.1), height: "100%" }}>
      {heading ? <StoreHeading>{heading}</StoreHeading> : null}
      {items.length === 0 ? (
        <SectionEmpty
          title="No columns yet"
          hint="Fill in a column title and body in this section's settings to show it here."
        />
      ) : (
        <div
          style={{
            display: "grid",
            gridTemplateColumns: stacked
              ? "minmax(0, 1fr)"
              : `repeat(${Math.min(columns, items.length)}, minmax(0, 1fr))`,
            gap: step(0.9),
          }}
        >
          {items.map((item) => (
            <Panel key={item.slot} style={{ display: "flex", flexDirection: "column", gap: step(0.4) }}>
              <span
                aria-hidden
                style={{
                  width: step(1.5),
                  height: step(0.2),
                  background: T.accent,
                  borderRadius: T.radius,
                }}
              />
              {item.title ? (
                <StoreHeading level={3} size="md">
                  {item.title}
                </StoreHeading>
              ) : null}
              {item.body ? (
                <StoreText muted small>
                  {item.body}
                </StoreText>
              ) : null}
            </Panel>
          ))}
        </div>
      )}
    </section>
  );
}
