/** How a catalogue price reads on screen — integer minor units and micros through `minorUnits`,
 *  never through a float. */

import type { PriceOut } from "@/api/catalog";
import { formatMicros, formatMinor } from "@/lib/minorUnits";

export const KIND_LABEL: Record<PriceOut["kind"], string> = {
  flat: "Flat",
  per_seat: "Per seat",
  usage: "Usage",
};

export const INTERVAL_LABEL: Record<string, string> = {
  month: "Monthly",
  year: "Yearly",
  one_time: "One-time",
};

/** "INR 1,499.00", "INR 199.00 / seat", "INR 0.0025 / email (emails_sent)". */
export function priceLabel(price: PriceOut): string {
  if (price.kind === "usage") {
    const rate = price.unit_price_micros === null ? "—" : formatMicros(price.unit_price_micros, price.currency);
    const unit = price.unit ? ` / ${price.unit}` : "";
    return `${rate}${unit}${price.meter_key ? ` (${price.meter_key})` : ""}`;
  }
  const amount = price.amount_minor === null ? "—" : formatMinor(price.amount_minor, price.currency);
  return price.kind === "per_seat" ? `${amount} / ${price.unit ?? "seat"}` : amount;
}
