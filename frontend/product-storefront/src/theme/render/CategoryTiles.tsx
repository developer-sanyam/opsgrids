import { Link } from "react-router-dom";

import { SectionEmpty, StoreHeading, StoreText } from "./primitives";
import { bool, choice, clampNum, optional } from "./settings";
import { T, step } from "./tokens";
import type { SectionRenderProps } from "./types";

const STYLES = ["tile", "list"] as const;

/** Entry points into the catalogue. Each tile is one link, not a card with a link inside it. */
export function CategoryTiles({ settings, categories, stacked }: SectionRenderProps) {
  const heading = optional(settings, "heading");
  const limit = clampNum(settings, "limit", 6, 1, 12);
  const columns = clampNum(settings, "columns", 3, 2, 4);
  const showCount = bool(settings, "showCount", true);
  const style = choice(settings, "style", STYLES, "tile");
  const items = categories.slice(0, limit);

  return (
    <section style={{ display: "flex", flexDirection: "column", gap: step(1.1), height: "100%" }}>
      {heading ? <StoreHeading>{heading}</StoreHeading> : null}
      {items.length === 0 ? (
        <SectionEmpty title="No categories yet" hint="Categories appear here once the catalogue has lines in them." />
      ) : (
        <ul
          style={{
            listStyle: "none",
            margin: 0,
            padding: 0,
            display: "grid",
            gridTemplateColumns:
              stacked || style === "list" ? "minmax(0, 1fr)" : `repeat(${columns}, minmax(0, 1fr))`,
            gap: step(0.7),
          }}
        >
          {items.map((category) => (
            <li key={category.key}>
              <Link
                to={`/store/catalogue?category=${category.key}`}
                style={{
                  display: "flex",
                  flexDirection: style === "list" ? "row" : "column",
                  alignItems: style === "list" ? "center" : "flex-start",
                  justifyContent: "space-between",
                  gap: step(0.4),
                  height: "100%",
                  background: T.surface,
                  border: `1px solid ${T.border}`,
                  borderRadius: T.radius,
                  padding: step(1),
                  textDecoration: "none",
                }}
              >
                <span style={{ display: "flex", flexDirection: "column", gap: step(0.2) }}>
                  <StoreHeading level={3} size="md">
                    {category.label}
                  </StoreHeading>
                  {style === "tile" && category.blurb ? (
                    <StoreText muted small>
                      {category.blurb}
                    </StoreText>
                  ) : null}
                </span>
                {showCount ? (
                  <span
                    style={{
                      fontFamily: T.fontBody,
                      fontSize: "0.75rem",
                      fontWeight: 700,
                      color: T.accent,
                      whiteSpace: "nowrap",
                    }}
                  >
                    {category.line_count} {category.line_count === 1 ? "line" : "lines"}
                  </span>
                ) : null}
              </Link>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
