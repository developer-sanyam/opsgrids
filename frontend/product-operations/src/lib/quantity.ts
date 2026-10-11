/**
 * Exact stock quantities on the client (ADR-0014 §2).
 *
 * The API sends a quantity as a decimal string with at most three decimals (`"82.5"`). These
 * helpers never turn one into a float: comparisons go through integer milli-units (exact for any
 * stock a warehouse can hold — well inside `Number.MAX_SAFE_INTEGER`), and display groups the
 * whole part while keeping the fraction verbatim.
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

/** `82500` → `"82.5"` — the wire form, for fixture data and optimistic copies. */
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
  // The whole part is an integer well inside the safe range, so grouping it is exact.
  const grouped = new Intl.NumberFormat("en-IN").format(Number(whole));
  return `${sign}${grouped}${frac ? `.${frac}` : ""}`;
}

/** Stock value in minor units for display: quantity × unit value, rounded once. */
export function stockValueMinor(qty: Qty, unitValueMinor: number): number {
  return Math.round((qtyToMilli(qty) * unitValueMinor) / 1000);
}

/* ------------------------- typed input (manufacturing) ------------------------- */

const QTY_INPUT = /^\d+(\.\d{1,3})?$/;
const PCT_INPUT = /^\d{1,3}(\.\d{1,2})?$/;

/** What the user typed, trimmed. The server owns canonical formatting; the client only decides
 *  whether the text is sendable, and sends it as a string. */
export function cleanDecimal(raw: string): string {
  return raw.trim();
}

/** True for a non-negative decimal with at most three decimals — a quantity the API accepts. */
export function isQtyInput(raw: string): boolean {
  return QTY_INPUT.test(cleanDecimal(raw));
}

/** True for a percentage the API accepts: digits with at most two decimals, 0 to 100 inclusive.
 *  Compared as text, never as a float. */
export function isPctInput(raw: string): boolean {
  const value = cleanDecimal(raw);
  if (!PCT_INPUT.test(value)) return false;
  const [whole = "", frac = ""] = value.split(".");
  if (whole.length < 3) return true;
  return whole === "100" && /^0*$/.test(frac);
}

/** True when a valid quantity string is exactly zero. */
export function isZeroQty(raw: string): boolean {
  return isQtyInput(raw) && /^0+(\.0+)?$/.test(cleanDecimal(raw));
}

/**
 * The inline message for a quantity field, or `undefined` when it is fine. Mirrors the server:
 * at most three decimals, never negative. `allowZero` is for fields where zero is a meaningful
 * value; a quantity of work or stock almost never is.
 */
export function qtyFieldError(raw: string, options: { allowZero?: boolean } = {}): string | undefined {
  const value = cleanDecimal(raw);
  if (value === "") return "Enter a quantity.";
  if (!isQtyInput(value)) return "Enter a number with at most three decimals, like 82.5.";
  if (!options.allowZero && isZeroQty(value)) return "Enter a quantity greater than zero.";
  return undefined;
}

/** The inline message for a percentage field, or `undefined`. */
export function pctFieldError(raw: string): string | undefined {
  const value = cleanDecimal(raw);
  if (value === "") return "Enter a percentage.";
  if (!isPctInput(value)) return "Enter a percentage from 0 to 100 with at most two decimals, like 2.5.";
  return undefined;
}

/** `"2.5"` → `"2.5%"`. The figure is shown exactly as sent. */
export function formatPercent(pct: string): string {
  return `${pct}%`;
}
