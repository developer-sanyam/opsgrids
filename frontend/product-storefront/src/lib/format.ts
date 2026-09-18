/**
 * Money and catalogue formatting. Amounts arrive as integer minor units with an ISO currency
 * (platform-wide rule) and are only ever divided by 100 at the moment they are printed.
 */

import type { Product } from "@/api/types";

const INR = new Intl.NumberFormat("en-IN", {
  style: "currency",
  currency: "INR",
  maximumFractionDigits: 0,
});

/** ₹1,240 — whole rupees, Indian digit grouping. */
export function formatMinor(minor: number, currency: Product["currency"] = "INR"): string {
  if (currency !== "INR") {
    return new Intl.NumberFormat("en-IN", { style: "currency", currency, maximumFractionDigits: 0 })
      .format(minor / 100);
  }
  return INR.format(minor / 100);
}

/** "₹1,240" for a fixed price, "₹1,240 – ₹1,480" for a band. */
export function formatPriceBand(product: Product): string {
  const low = formatMinor(product.price_min_minor, product.currency);
  if (product.price_max_minor <= product.price_min_minor) return low;
  return `${low} – ${formatMinor(product.price_max_minor, product.currency)}`;
}

/** The GST basis, said in words. Colour never carries this; the words do. */
export function gstLabel(product: Product): string {
  return product.gst_inclusive ? "incl. GST" : "excl. GST";
}

/** "MOQ 500 pcs". */
export function moqLabel(product: Product): string {
  return `MOQ ${new Intl.NumberFormat("en-IN").format(product.moq)} ${product.moq_unit}`;
}

/** The availability fact, as a sentence rather than a dot. */
export function availabilityLabel(product: Product): string {
  if (product.in_stock) return "In stock";
  if (product.lead_time_days === 1) return "Lead time 1 working day";
  return `Lead time ${product.lead_time_days} working days`;
}

/* --------------------------------- retail --------------------------------- */

/**
 * The saving off MRP as a whole percentage, or `null` when there is no honest claim to make.
 *
 * **Floors, never rounds.** A saving of 26.6% rounded reads "27% OFF", which claims more than the
 * customer gets; floored it reads "26% OFF", which claims less. When a printed number has to be
 * wrong, the honest direction to err is down — the shopper is never worse off than the badge said.
 *
 * Returns `null` in three cases, and this single guard is what prevents every "₹69, was ₹59"
 * defect downstream — a caller that gets `null` has nothing to render and cannot render nonsense:
 *
 * - no MRP is published;
 * - the MRP is not *above* the selling price (equal, or lower, which is a data error, not a
 *   negative discount to display);
 * - the saving floors to 0, i.e. under 1%, where "0% OFF" is a worse thing to print than nothing.
 */
export function discountPercent(product: Product): number | null {
  const mrp = product.mrp_minor;
  if (mrp === null) return null;
  if (mrp <= product.price_min_minor) return null;
  const percent = Math.floor(((mrp - product.price_min_minor) / mrp) * 100);
  return percent > 0 ? percent : null;
}

/** The struck-through MRP, or `null` when there is no MRP worth striking through. */
export function formatMrp(product: Product): string | null {
  if (product.mrp_minor === null) return null;
  if (product.mrp_minor <= product.price_min_minor) return null;
  return formatMinor(product.mrp_minor, product.currency);
}

/**
 * The delivery promise in words: `"8 mins"`, `"1 hr 10 mins"`, `"2 hrs"`.
 *
 * `null` when the line carries no promise — most of the catalogue, and every made-to-order line.
 * A missing promise is never rendered as "0 mins" or "soon"; the chip simply does not appear.
 */
export function deliveryEtaLabel(product: Product): string | null {
  const total = product.delivery_eta_minutes;
  if (total === null || total <= 0) return null;

  const hours = Math.floor(total / 60);
  const minutes = total % 60;
  const hourPart = hours === 1 ? "1 hr" : `${hours} hrs`;
  const minutePart = minutes === 1 ? "1 min" : `${minutes} mins`;

  if (hours === 0) return minutePart;
  if (minutes === 0) return hourPart;
  return `${hourPart} ${minutePart}`;
}

/**
 * The price to show for the selected pack.
 *
 * Falls back to the product's own band whenever the selection cannot be honoured — no selection,
 * no variants, or an id that no longer exists because the catalogue moved under a stale selection.
 * Falling back is right rather than showing nothing: the product-level band is the canonical
 * default-pack price by the invariant on `Product.variants`, so the fallback is never a lie.
 */
export function variantPrice(product: Product, variantId: string | null): string {
  if (variantId === null) return formatPriceBand(product);
  const variant = product.variants.find((v) => v.id === variantId);
  if (variant === undefined) return formatPriceBand(product);
  if (variant.price_max_minor <= variant.price_min_minor) {
    return formatMinor(variant.price_min_minor, product.currency);
  }
  return `${formatMinor(variant.price_min_minor, product.currency)} – ${formatMinor(variant.price_max_minor, product.currency)}`;
}
