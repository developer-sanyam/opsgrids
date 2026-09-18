import { StoreHeading, StoreText } from "./primitives";
import { choice, clampNum, lines, optional } from "./settings";
import { step } from "./tokens";
import type { SectionRenderProps } from "./types";

const ALIGNS = ["left", "center"] as const;

/** A heading and prose. Blank lines in the body become paragraphs; nothing is parsed as markup. */
export function RichText({ settings }: SectionRenderProps) {
  const align = choice(settings, "align", ALIGNS, "left");
  const columns = clampNum(settings, "columns", 1, 1, 2);
  const heading = optional(settings, "heading");
  const paragraphs = lines(settings, "body");

  return (
    <section
      style={{
        display: "flex",
        flexDirection: "column",
        gap: step(0.9),
        alignItems: align === "center" ? "center" : "flex-start",
        textAlign: align === "center" ? "center" : "left",
        height: "100%",
        justifyContent: "center",
      }}
    >
      {heading ? <StoreHeading>{heading}</StoreHeading> : null}
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
            muted
            {...(columns === 2 ? { style: { marginBottom: step(0.6), maxWidth: "none" } } : {})}
          >
            {paragraph}
          </StoreText>
        ))}
      </div>
    </section>
  );
}
