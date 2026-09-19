/**
 * Money, in and out of a form.
 *
 * Amounts are **integer minor units with an ISO currency** everywhere except the two characters
 * either side of the input box (ADR-0000 §8). Nothing here holds a fractional rupee, and the
 * parse is done on the digits of the string rather than by multiplying a float by 100 — `29.35 *
 * 100` is `2934.9999999999995`, and `Math.round` hiding that is the kind of fix that works until
 * the amount is large enough that it does not.
 *
 * `@/lib/format`'s `formatMinor` is the catalogue's money face and floors to whole rupees
 * (`maximumFractionDigits: 0`), which is right for a price band and wrong here: a delivery fee of
 * ₹29.50 rendered as "₹30" is a number the buyer was not shown. This module formats the paise when
 * there are paise and omits them when there are not.
 */

/**
 * Print an integer minor amount. `2900` → `₹29`, `2950` → `₹29.50`.
 *
 * The decimals appear only when they carry information, which keeps a column of whole-rupee fees
 * readable and still never rounds a real paise value away.
 */
export function formatMinorExact(minor: number, currency: string): string {
  const fractionDigits = minor % 100 === 0 ? 0 : 2;
  try {
    return new Intl.NumberFormat("en-IN", {
      style: "currency",
      currency,
      minimumFractionDigits: fractionDigits,
      maximumFractionDigits: fractionDigits,
    }).format(minor / 100);
  } catch {
    // An unrecognised ISO code must not blank the amount out — the number is the fact, the symbol
    // is decoration. `Intl` throws on a bad currency code rather than falling back.
    return `${(minor / 100).toFixed(fractionDigits)} ${currency}`;
  }
}

/** The value a money input shows for an amount already stored: `2950` → `"29.50"`, `2900` → `"29"`. */
export function minorToInput(minor: number | null): string {
  if (minor === null) return "";
  const sign = minor < 0 ? "-" : "";
  const absolute = Math.abs(minor);
  const whole = Math.trunc(absolute / 100);
  const paise = absolute % 100;
  return paise === 0 ? `${sign}${whole}` : `${sign}${whole}.${String(paise).padStart(2, "0")}`;
}

export type MoneyParse =
  | { ok: true; minor: number | null }
  | { ok: false; message: string };

/**
 * Read a merchant's typed amount into integer minor units, on the string.
 *
 * Empty is a legitimate answer — "no delivery fee configured" is not "a fee of zero" — so it
 * returns `null` rather than `0`. Spaces, commas and a currency symbol are tolerated because
 * people paste amounts; anything past two decimal places is refused rather than rounded, since
 * silently turning ₹29.999 into ₹30 is inventing a number the merchant did not type.
 */
export function parseMinor(input: string): MoneyParse {
  const cleaned = input.replace(/[\s,₹]/g, "");
  if (cleaned === "") return { ok: true, minor: null };

  const match = /^(\d+)(?:\.(\d{1,2}))?$/.exec(cleaned);
  if (!match) {
    return {
      ok: false,
      message: "Enter an amount in rupees, like 29 or 29.50. No more than two decimal places.",
    };
  }
  const whole = match[1] ?? "0";
  const fraction = (match[2] ?? "").padEnd(2, "0");
  const minor = Number(whole) * 100 + Number(fraction);
  if (!Number.isSafeInteger(minor)) {
    return { ok: false, message: "That amount is too large." };
  }
  return { ok: true, minor };
}
