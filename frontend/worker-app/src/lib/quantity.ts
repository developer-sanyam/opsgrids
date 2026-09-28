/**
 * Exact stock quantities on the client (ADR-0014 §2).
 *
 * The API sends a quantity as a decimal string with at most three decimals (`"82.5"`). These
 * helpers never turn one into a float: comparisons go through integer milli-units (exact for any
 * stock a warehouse can hold — well inside `Number.MAX_SAFE_INTEGER`), and display groups the
 * whole part while keeping the fraction verbatim.
 *
 * Copied from `product-operations/src/lib/quantity.ts` rather than shared, the same way that
 * file's own types are mirrored rather than imported — this app has no dependency on
 * `product-operations`, by design (ADR-0024 §7: a separate app, a separate deploy).
 */

import type { Qty } from "@/api/types";

const QTY = /^(-?)(\d+)(?:\.(\d{1,3}))?$/;

/** `"82.5"` → `82500`. Throws on anything the API would never send. */
export function qtyToMilli(qty: Qty): number {
  const match = QTY.exec(qty);
  if (!match) throw new Error(`Not a quantity: ${qty}`);
  const [, sign, whole = "0", frac = ""] = match;
  const milli = Number(whole) * 1000 + Number(frac.padEnd(3, "0"));
  return sign === "-" ? -milli : milli;
}

/** `82500` → `"82.5"` — the wire form. */
export function milliToQty(milli: number): Qty {
  const sign = milli < 0 ? "-" : "";
  const abs = Math.abs(milli);
  const whole = Math.floor(abs / 1000);
  const frac = abs % 1000;
  return frac === 0
    ? `${sign}${whole}`
    : `${sign}${whole}.${String(frac).padStart(3, "0").replace(/0+$/, "")}`;
}

/** `"12500.75"` → `"12,500.75"`. The fraction is shown exactly as sent. */
export function formatQty(qty: Qty): string {
  const match = QTY.exec(qty);
  if (!match) return qty;
  const [, sign, whole = "0", frac] = match;
  const grouped = new Intl.NumberFormat("en-IN").format(Number(whole));
  return `${sign}${grouped}${frac ? `.${frac}` : ""}`;
}
