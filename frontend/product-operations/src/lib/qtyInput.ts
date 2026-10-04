/**
 * Validating a quantity a person typed (ADR-0014 §2).
 *
 * The result is always the exact decimal **string** the API takes — never a `number`. The text is
 * checked as text (digits, at most one point, at most three decimals) and only then measured in
 * integer milli-units for comparisons, so `0.1 + 0.2` style drift cannot occur. The messages mirror
 * the server's own refusals; the server stays the authority and a refusal it sends is mapped onto
 * the same field.
 */

import type { Qty } from "@/api/types";
import { milliToQty, qtyToMilli } from "@/lib/quantity";

/** Units a stock can only hold in whole numbers — the registry's `discrete` units, mirrored from
 *  `app/inventory/quantity.py`. A unit this list does not name is a measure, held to 3 decimals. */
const DISCRETE_UNITS: ReadonlySet<string> = new Set(["pcs", "dozen", "box", "carton", "bundle", "set", "roll"]);

/** Whether `uom` (an item's unit code, any case) can only be held in whole units. */
export function isDiscreteUom(uom: string): boolean {
  return DISCRETE_UNITS.has(uom.trim().toLowerCase());
}

/** The most milli-units a stock figure may hold: the server's own headroom under a BIGINT. */
const MAX_MILLI = 10 ** 15;

const TYPED = /^(\d+)(?:\.(\d*))?$/;

export type QtyCheck =
  | { ok: true; /** The exact wire string, normalised (`"082.50"` becomes `"82.5"`). */ value: Qty; milli: number }
  | { ok: false; message: string };

export interface QtyRules {
  /** The unit the quantity is in; a discrete unit refuses a fraction. */
  uom: string;
  /** Refuse a quantity above this one (milli-units), e.g. what a bucket holds. */
  maxMilli?: number | undefined;
  /** What the cap is called in the message: "on hand", "available". */
  maxLabel?: string | undefined;
  /** Allow `0`. Default false: a stock operation of nothing is refused by the server. */
  allowZero?: boolean | undefined;
}

/** Check one typed quantity. Never throws. */
export function checkQuantity(raw: string, rules: QtyRules): QtyCheck {
  const text = raw.trim();
  if (text === "") return { ok: false, message: "Enter a quantity." };
  const match = TYPED.exec(text);
  if (!match || match[2] === "") {
    return { ok: false, message: "Use digits and an optional decimal point, like 82.5." };
  }
  const whole = match[1] ?? "0";
  const frac = match[2] ?? "";
  if (frac.length > 3) return { ok: false, message: "Use at most 3 decimal places." };
  if (whole.length > 12) return { ok: false, message: "That quantity is too large." };
  const milli = Number(whole) * 1000 + Number(frac.padEnd(3, "0"));
  if (!Number.isSafeInteger(milli) || milli > MAX_MILLI) {
    return { ok: false, message: "That quantity is too large." };
  }
  if (isDiscreteUom(rules.uom) && milli % 1000 !== 0) {
    return { ok: false, message: `${rules.uom} are counted in whole units — remove the decimals.` };
  }
  if (milli === 0 && rules.allowZero !== true) {
    return { ok: false, message: "Quantity must be greater than zero." };
  }
  if (rules.maxMilli !== undefined && milli > rules.maxMilli) {
    const label = rules.maxLabel ?? "available";
    return {
      ok: false,
      message: `That is more than the ${milliToQty(rules.maxMilli)} ${rules.uom} ${label}.`,
    };
  }
  return { ok: true, value: milliToQty(milli), milli };
}

/** The sum of several quantity strings, in milli-units. A string that is not a valid quantity
 *  counts as zero — the per-row check says why it is not. */
export function sumMilli(values: readonly string[]): number {
  let total = 0;
  for (const value of values) {
    const checked = checkQuantity(value, { uom: "", allowZero: true });
    if (checked.ok) total += checked.milli;
  }
  return total;
}

/** The part of a bucket that can leave it: on hand less what is promised (ADR-0014 §11), in
 *  milli-units. Reserved stock cannot be transferred, re-statused, split or merged away. */
export function movableMilli(bucket: { on_hand_qty: Qty; reserved_qty: Qty }): number {
  return Math.max(qtyToMilli(bucket.on_hand_qty) - qtyToMilli(bucket.reserved_qty), 0);
}

/** A positive whole number typed into a measurement field (width in mm, GSM). */
export type WholeCheck = { ok: true; value: number } | { ok: false; message: string };

export function checkWholeNumber(raw: string, label: string, max = 100_000): WholeCheck {
  const text = raw.trim();
  if (!/^\d+$/.test(text)) return { ok: false, message: `${label} is a whole number, like 150.` };
  const value = Number(text);
  if (value < 1 || value > max) return { ok: false, message: `${label} is between 1 and ${max}.` };
  return { ok: true, value };
}
