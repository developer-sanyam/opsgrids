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
