import { availabilityLabel, formatPriceBand, moqLabel } from "@/lib/format";
import { SectionEmpty, StoreHeading, StoreText } from "./primitives";
import { emptySourceHint, resolveProducts } from "./products";
import { bool, choice, clampNum, optional, str } from "./settings";
import { T, step } from "./tokens";
import type { SectionRenderProps } from "./types";

const PRICE_NOTES = ["exclusive", "inclusive", "on-request"] as const;

const cell = {
  padding: `${step(0.55)} ${step(0.7)}`,
  fontFamily: T.fontBody,
  fontSize: "0.875rem",
  borderBottom: `1px solid ${T.border}`,
  textAlign: "left",
} as const;

/** A real `<table>`: this is tabular data, and a buyer reads down the price column. */
export function PriceTable({ settings, products }: SectionRenderProps) {
  const heading = optional(settings, "heading");
  const source = str(settings, "source", "all");
  const limit = clampNum(settings, "limit", 8, 1, 48);
  const showMoq = bool(settings, "showMoq", true);
  const showUnit = bool(settings, "showUnit", true);
  const priceNote = choice(settings, "priceNote", PRICE_NOTES, "exclusive");
  const note = optional(settings, "note");
  const items = resolveProducts(source, products, limit);

  const basisLabel =
    priceNote === "inclusive" ? "Price (incl. GST)" : priceNote === "exclusive" ? "Price (excl. GST)" : "Price";

  return (
    <section style={{ display: "flex", flexDirection: "column", gap: step(1), height: "100%" }}>
      {heading ? <StoreHeading>{heading}</StoreHeading> : null}
      {items.length === 0 ? (
        <SectionEmpty title="No lines to price" hint={emptySourceHint(source)} />
      ) : (
        <div style={{ overflowX: "auto", border: `1px solid ${T.border}`, borderRadius: T.radius }}>
          <table style={{ width: "100%", borderCollapse: "collapse", background: T.surface }}>
            <caption style={{ position: "absolute", width: 1, height: 1, overflow: "hidden", clip: "rect(0 0 0 0)" }}>
              {heading ?? "Price list"}
            </caption>
            <thead>
              <tr>
                <th scope="col" style={{ ...cell, color: T.muted, fontWeight: 700 }}>
                  Line
                </th>
                {showUnit ? (
                  <th scope="col" style={{ ...cell, color: T.muted, fontWeight: 700 }}>
                    Unit
                  </th>
                ) : null}
                {showMoq ? (
                  <th scope="col" style={{ ...cell, color: T.muted, fontWeight: 700 }}>
                    MOQ
                  </th>
                ) : null}
                <th scope="col" style={{ ...cell, color: T.muted, fontWeight: 700 }}>
                  Availability
                </th>
                <th scope="col" style={{ ...cell, color: T.muted, fontWeight: 700, textAlign: "right" }}>
                  {basisLabel}
                </th>
              </tr>
            </thead>
            <tbody>
              {items.map((product) => (
                <tr key={product.id}>
                  <th scope="row" style={{ ...cell, color: T.text, fontWeight: 600 }}>
                    {product.name}
                    <span style={{ display: "block", color: T.muted, fontWeight: 400, fontSize: "0.75rem" }}>
                      {product.code}
                    </span>
                  </th>
                  {showUnit ? <td style={{ ...cell, color: T.muted }}>{product.unit}</td> : null}
                  {showMoq ? <td style={{ ...cell, color: T.muted }}>{moqLabel(product)}</td> : null}
                  <td style={{ ...cell, color: T.muted }}>{availabilityLabel(product)}</td>
                  <td
                    style={{
                      ...cell,
                      color: T.text,
                      textAlign: "right",
                      fontWeight: 700,
                      fontVariantNumeric: "tabular-nums",
                    }}
                  >
                    {priceNote === "on-request" ? "On request" : formatPriceBand(product)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {note ? (
        <StoreText muted small>
          {note}
        </StoreText>
      ) : null}
    </section>
  );
}
