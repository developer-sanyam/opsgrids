/**
 * Typed-in quantities for the movement screens (transfers, reservations, adjustments).
 *
 * What a person types is parsed once, here, into the decimal string the API takes (`"82.5"`). It is
 * never passed through `Number`/`parseFloat`: comparisons go through `qtyToMilli`'s exact integer
 * milli-units, and the string that is sent is the string that was validated.
 */

import type { Qty, UomOut } from "@/api/types";
import { milliToQty, qtyToMilli } from "./quantity";

export type QtyParse = { ok: true; value: Qty } | { ok: false; message: string };

export interface QtyRules {
  /** A leading minus is allowed (an adjustment's delta). */
  signed?: boolean;
  /** Zero is allowed (a receipt line with nothing received, a shortage of none). */
  allowZero?: boolean;
  /** The item's unit is counted in whole units — no fraction. */
  whole?: boolean;
  /** Names the unit in the message, when there is one. */
  uom?: string;
}

/**
 * Check a typed quantity against the wire format (a decimal with at most three places) and the
 * item's unit. The server still has the last word — a unit's own limits are its call — this
 * rejects what could never be a quantity, and says why, before a round trip.
 */
export function parseQty(input: string, rules: QtyRules = {}): QtyParse {
  const text = input.trim();
  const pattern = rules.signed ? /^-?\d+(?:\.\d{1,3})?$/ : /^\d+(?:\.\d{1,3})?$/;
  if (!pattern.test(text)) {
    return {
      ok: false,
      message: rules.signed
        ? "Enter a number, negative to take stock out, with at most 3 decimal places."
        : "Enter a number with at most 3 decimal places.",
    };
  }
  const milli = qtyToMilli(text);
  if (rules.whole && milli % 1000 !== 0) {
    return {
      ok: false,
      message: `${rules.uom ?? "This unit"} is counted in whole units — no decimals.`,
    };
  }
  if (milli === 0 && rules.allowZero !== true) {
    return { ok: false, message: "The quantity can't be zero." };
  }
  return { ok: true, value: milliToQty(milli) };
}

/** `a` against `b`, exactly: negative when `a` is smaller, zero when equal, positive when larger. */
export function compareQty(a: Qty, b: Qty): number {
  return qtyToMilli(a) - qtyToMilli(b);
}

/** `a + b` as a wire quantity. */
export function addQty(a: Qty, b: Qty): Qty {
  return milliToQty(qtyToMilli(a) + qtyToMilli(b));
}

/** `a - b` as a wire quantity. */
export function subtractQty(a: Qty, b: Qty): Qty {
  return milliToQty(qtyToMilli(a) - qtyToMilli(b));
}

/** True when `qty` is exactly zero. */
export function isZeroQty(qty: Qty): boolean {
  return qtyToMilli(qty) === 0;
}

/** The unit codes the organization counts in whole units only, from its UOM directory. */
export function discreteUomCodes(uoms: readonly UomOut[]): ReadonlySet<string> {
  return new Set(uoms.filter((u) => u.is_discrete).map((u) => u.code));
}
