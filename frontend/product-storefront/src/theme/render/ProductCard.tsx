import type { Product } from "@/api/types";
import { availabilityLabel, formatPriceBand, gstLabel, moqLabel } from "@/lib/format";
import { ImagePlate, MetaTag, StoreHeading, StoreText } from "./primitives";
import { T, step } from "./tokens";

/**
 * One catalogue line, as a buyer reads it: name, part code, the one-line spec, price with its GST
 * basis, MOQ, and availability said in words. Availability is never a coloured dot on its own —
 * "In stock" and "Lead time 7 working days" are different facts and both are written out.
 */
export function ProductCard({
  product,
  showCode = true,
  showPrice = true,
  showStock = true,
  priceNote,
}: {
  product: Product;
  showCode?: boolean;
  showPrice?: boolean;
  showStock?: boolean;
  /** Overrides the product's own GST basis — a merchant may publish a whole grid one way. */
  priceNote?: "exclusive" | "inclusive" | "on-request";
}) {
  const basis =
    priceNote === "inclusive" ? "incl. GST" : priceNote === "exclusive" ? "excl. GST" : gstLabel(product);

  return (
    <article
      style={{
        background: T.surface,
        border: `1px solid ${T.border}`,
        borderRadius: T.radius,
        overflow: "hidden",
        display: "flex",
        flexDirection: "column",
        height: "100%",
      }}
    >
      <ImagePlate seed={product.code} style={{ aspectRatio: "16 / 10", minHeight: 0, border: "none", borderRadius: 0 }} />
      <div style={{ display: "flex", flexDirection: "column", gap: step(0.35), padding: step(0.9), flex: 1 }}>
        {showCode ? (
          <span style={{ fontFamily: T.fontBody, fontSize: "0.6875rem", letterSpacing: "0.08em", color: T.muted }}>
            {product.code} · {product.brand}
          </span>
        ) : null}
        <StoreHeading level={3} size="sm">
          {product.name}
        </StoreHeading>
        <StoreText muted small style={{ flex: 1 }}>
          {product.spec}
        </StoreText>
        {showPrice ? (
          <div style={{ display: "flex", alignItems: "baseline", gap: step(0.35), flexWrap: "wrap" }}>
            <span
              style={{
                fontFamily: T.fontHeading,
                fontSize: "1.05rem",
                fontWeight: 700,
                color: T.text,
                fontVariantNumeric: "tabular-nums",
              }}
            >
              {priceNote === "on-request" ? "Price on request" : formatPriceBand(product)}
            </span>
            {priceNote === "on-request" ? null : (
              <span style={{ fontFamily: T.fontBody, fontSize: "0.75rem", color: T.muted }}>
                {product.unit} · {basis}
              </span>
            )}
          </div>
        ) : null}
        {showStock ? (
          <div style={{ display: "flex", gap: step(0.35), flexWrap: "wrap", marginTop: step(0.2) }}>
            <MetaTag tone={product.in_stock ? "accent" : "quiet"}>{availabilityLabel(product)}</MetaTag>
            <MetaTag>{moqLabel(product)}</MetaTag>
          </div>
        ) : null}
      </div>
    </article>
  );
}
