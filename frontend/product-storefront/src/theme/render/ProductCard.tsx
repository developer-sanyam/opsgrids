import { useEffect, useRef, useState } from "react";

import type { Product } from "@/api/types";
import { lineDraft, lineKey, useLineList } from "@/store/lineList";
import {
  availabilityLabel,
  deliveryEtaLabel,
  discountPercent,
  formatMinor,
  formatMrp,
  formatPriceBand,
  gstLabel,
  moqLabel,
  variantPrice,
} from "@/lib/format";
import type { StoreMode } from "../schema";
import { DEFAULT_CARD_OPTIONS, stepQuantity, type CardOptions } from "./cardOptions";
import {
  ActionButton,
  DiscountBadge,
  ImagePlate,
  MetaTag,
  PriceWithMrp,
  QuantityStepper,
  StoreHeading,
  StoreText,
  VariantSelect,
  VisuallyHidden,
} from "./primitives";
import { T, step } from "./tokens";

export type ProductCardProps = Partial<CardOptions> & {
  product: Product;
  /** Decides the vocabulary: what the button says, what the meta row carries, how price reads. */
  mode: StoreMode;
  showCode?: boolean;
  showPrice?: boolean;
  showStock?: boolean;
};

/**
 * One catalogue line, as a buyer reads it — in whichever of the two languages this store speaks.
 *
 * **Trade**: part code, the one-line spec, a price band with its GST basis, MOQ, and availability
 * said in words. Availability is never a coloured dot on its own — "In stock" and "Lead time 7
 * working days" are different facts and both are written out. The primary action adds to a line
 * list, which ends in an enquiry.
 *
 * **Retail**: a photo, a discount flag, the selling price against a struck-through MRP, a delivery
 * promise, a pack-size selector and ADD.
 *
 * The flag and the delivery chip require **both** `mode === "retail"` and non-null data. A fixture
 * or a bad import that leaves `delivery_eta_minutes` set on a trade line must never let a trade
 * store promise a delivery time it has no way to keep.
 *
 * **What the primary action does, exactly.** It opens a line in the in-page list, steps it up and
 * down, moves the count in the announcement bar and announces the change. It does not survive a
 * reload, reach a server, place an order, reserve stock or take payment — see `store/lineList.tsx`,
 * where that scope is the first thing written. Retail steps in ones; trade steps in whole MOQs,
 * because a trade buyer cannot order below the minimum and a stepper that starts at 1 would offer
 * them a quantity the merchant will not accept.
 *
 * Outside a `LineListProvider` — the gallery's miniature, the editor's canvas, a test — the hook
 * hands back a no-op and the card stays exactly what it was: ADD paints, pressing it does nothing.
 */
export function ProductCard({
  product,
  mode,
  showCode = true,
  showPrice = true,
  showStock = true,
  cardStyle = DEFAULT_CARD_OPTIONS.cardStyle,
  showBadge = DEFAULT_CARD_OPTIONS.showBadge,
  showEta = DEFAULT_CARD_OPTIONS.showEta,
  showVariants = DEFAULT_CARD_OPTIONS.showVariants,
  priceNote = DEFAULT_CARD_OPTIONS.priceNote,
  size = DEFAULT_CARD_OPTIONS.size,
}: ProductCardProps) {
  const retail = mode === "retail";
  const compact = cardStyle === "compact";

  const variants = retail && showVariants ? product.variants : [];
  const hasSelector = variants.length >= 2;
  const [chosenId, setChosen] = useState<string>(variants[0]?.id ?? "");
  /* An id that matches nothing — a card re-used for a different line without remounting — falls
     back to the default pack rather than to a `<select>` with no matching option. */
  const chosenIndex = hasSelector ? Math.max(0, variants.findIndex((variant) => variant.id === chosenId)) : -1;
  const chosen = chosenIndex >= 0 ? (variants[chosenIndex] ?? null) : null;

  const basis =
    priceNote === "inclusive" ? "incl. GST" : priceNote === "exclusive" ? "excl. GST" : gstLabel(product);

  /* A chosen variant prices itself; the product's own band is the answer when there is no selector
     or the buyer has not moved it. MRP and the flag follow the same selection, so the card can
     never show one pack's price struck against another pack's MRP. */
  const price = chosen ? variantPrice(product, chosen.id) : formatPriceBand(product);
  const mrp = !retail
    ? null
    : chosen && chosenIndex > 0
      ? chosen.mrp_minor === null
        ? null
        : formatMinor(chosen.mrp_minor, product.currency)
      : formatMrp(product);
  const discount = retail && showBadge ? discountPercent(product) : null;
  const flag = discount !== null && chosenIndex <= 0 ? discount : null;
  const eta = retail && showEta && product.delivery_eta_minutes !== null ? deliveryEtaLabel(product) : null;

  const actionLabel = retail ? "ADD" : "Add to line list";

  /* The list, or the no-op when there is no provider above. Nothing below branches on which. */
  const list = useLineList();
  /* The chosen pack is part of the line's identity: 500 g and 1 kg of the same product are two
     lines, and moving the selector moves the card to the other one. */
  const key = lineKey(product.id, chosen?.id ?? null);
  const quantity = list.qtyOf(key);
  const stepSize = stepQuantity(product, mode);

  /* Trade counts in its own unit — "500 pcs", not "500" — because a trade quantity without its
     unit is ambiguous in exactly the direction that costs money. */
  const quantityWords = retail ? `${quantity}` : `${quantity} ${product.moq_unit}`;

  /* Each control says what it does *and* to which line. "+" alone is unusable by voice or by
     screen reader in a grid of twenty cards, and "one more" would be a lie in trade, where one
     press is a whole minimum order quantity. */
  const increaseLabel = retail
    ? `Add one more ${product.name}`
    : `Add ${stepSize} ${product.moq_unit} of ${product.name}`;
  const decreaseLabel = retail
    ? `Remove one from ${product.name}`
    : `Remove ${stepSize} ${product.moq_unit} from ${product.name}`;

  const [announcement, setAnnouncement] = useState("");
  const announced = useRef({ key, quantity });

  /*
   * The announcement follows what the list actually holds, not what was clicked.
   *
   * Announcing from inside the handler would have the card tell a screen reader "2 added" in the
   * gallery's miniature, where the press went to a no-op and nothing was added at all. Reading the
   * quantity back means the sentence is true wherever the card is mounted.
   *
   * Silent on mount — a live region only announces changes made while it is in the tree — and
   * silent when the *line* changes rather than the quantity, so moving the pack selector to an
   * unopened pack does not report a removal that never happened.
   */
  useEffect(() => {
    const previous = announced.current;
    announced.current = { key, quantity };
    if (previous.key !== key || previous.quantity === quantity) return;
    setAnnouncement(
      quantity === 0 ? `${product.name} removed` : `${product.name}, quantity ${quantityWords}`,
    );
  }, [key, quantity, quantityWords, product.name]);

  return (
    <article
      style={{
        position: "relative",
        background: T.surface,
        border: `1px solid ${T.border}`,
        borderRadius: T.radius,
        overflow: "hidden",
        display: "flex",
        flexDirection: "column",
        height: "100%",
      }}
    >
      <div style={{ position: "relative" }}>
        <ImagePlate
          seed={product.code}
          src={product.image_url}
          style={{ aspectRatio: "16 / 10", minHeight: 0, border: "none", borderRadius: 0 }}
        />
        {flag === null ? null : (
          <DiscountBadge percent={flag} style={{ position: "absolute", top: 0, insetInlineStart: step(0.5) }} />
        )}
      </div>
      <div
        style={{
          display: "flex",
          flexDirection: "column",
          gap: step(compact ? 0.3 : 0.35),
          padding: step(compact ? 0.6 : 0.9),
          flex: 1,
        }}
      >
        {showCode && !retail ? (
          <span style={{ fontFamily: T.fontBody, fontSize: "0.6875rem", letterSpacing: "0.08em", color: T.muted }}>
            {product.code} · {product.brand}
          </span>
        ) : null}
        <StoreHeading level={3} size="sm">
          {product.name}
        </StoreHeading>
        {compact ? null : (
          <StoreText muted small style={{ flex: 1 }}>
            {product.spec}
          </StoreText>
        )}
        {eta === null ? null : (
          <div style={{ display: "flex", gap: step(0.35), flexWrap: "wrap" }}>
            <MetaTag>{eta}</MetaTag>
          </div>
        )}
        {showPrice ? (
          <div style={{ display: "flex", alignItems: "baseline", gap: step(0.35), flexWrap: "wrap" }}>
            {priceNote === "on-request" ? (
              <span
                style={{
                  fontFamily: T.fontHeading,
                  fontSize: "1.05rem",
                  fontWeight: 700,
                  color: T.text,
                  fontVariantNumeric: "tabular-nums",
                }}
              >
                Price on request
              </span>
            ) : retail ? (
              <PriceWithMrp price={price} mrp={mrp} />
            ) : (
              <>
                <span
                  style={{
                    fontFamily: T.fontHeading,
                    fontSize: "1.05rem",
                    fontWeight: 700,
                    color: T.text,
                    fontVariantNumeric: "tabular-nums",
                  }}
                >
                  {price}
                </span>
                <span style={{ fontFamily: T.fontBody, fontSize: "0.75rem", color: T.muted }}>
                  {product.unit} · {basis}
                </span>
              </>
            )}
          </div>
        ) : null}
        {showStock && !retail ? (
          <div style={{ display: "flex", gap: step(0.35), flexWrap: "wrap", marginTop: step(0.2) }}>
            <MetaTag tone={product.in_stock ? "accent" : "quiet"}>{availabilityLabel(product)}</MetaTag>
            <MetaTag>{moqLabel(product)}</MetaTag>
          </div>
        ) : null}
        <div
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            gap: step(0.4),
            marginTop: "auto",
            paddingTop: step(0.5),
          }}
        >
          {hasSelector ? (
            <VariantSelect
              label={`Pack size, ${product.name}`}
              hideLabel
              value={chosen?.id ?? ""}
              onChange={setChosen}
              options={variants.map((variant) => ({
                value: variant.id,
                label: variant.label,
                disabled: !variant.in_stock,
              }))}
            />
          ) : null}
          {/*
           * A line with no packs to choose between used to render an empty placeholder beside the
           * action, leaving a visible gap where the selector would have been and making cards in
           * one row read as two different layouts. The action takes that width instead — `grid`
           * because a grid child stretches on its own, which neither the button nor the stepper
           * would do as an inline-flex box.
           */}
          <div style={{ display: "grid", flex: hasSelector ? "0 0 auto" : "1 1 auto", minWidth: 0 }}>
            {quantity > 0 ? (
              <QuantityStepper
                size={size}
                value={quantityWords}
                decreaseLabel={decreaseLabel}
                increaseLabel={increaseLabel}
                onDecrease={() => list.setQty(key, quantity - stepSize)}
                onIncrease={() => list.add(lineDraft(product, chosen), stepSize)}
              />
            ) : (
              <ActionButton
                type="button"
                variant="outline"
                size={size}
                ariaLabel={`${actionLabel}: ${product.name}`}
                onClick={() => list.add(lineDraft(product, chosen), stepSize)}
              >
                {actionLabel}
              </ActionButton>
            )}
          </div>
          <VisuallyHidden live="polite">{announcement}</VisuallyHidden>
        </div>
      </div>
    </article>
  );
}
