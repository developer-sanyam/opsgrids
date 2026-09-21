/**
 * Money, in and out of a form.
 *
 * Amounts are integer minor units with an ISO currency everywhere except the digits inside a
 * money input (root `CLAUDE.md`: no floats, no `Numeric(_, 2)`). The parse works on the digits of
 * the typed string rather than `Math.round(parseFloat(x) * 100)` — `2250.99 * 100` is
 * `225098.99999999997` in IEEE 754, and rounding that away is the kind of fix that works until
 * the amount is large enough that it doesn't. Every money field on this product goes through this
 * one parser — a form that needs its own rules (a required line price, say) passes `options`
 * rather than growing a second implementation next to it.
 */

/** The value a money input shows for an amount already stored: `2950` -> `"29.50"`. */
export function minorToInput(minor: number): string {
  const sign = minor < 0 ? "-" : "";
  const absolute = Math.abs(minor);
  const whole = Math.trunc(absolute / 100);
  const cents = absolute % 100;
  return cents === 0 ? `${sign}${whole}` : `${sign}${whole}.${String(cents).padStart(2, "0")}`;
}

export type MoneyParse = { ok: true; minor: number } | { ok: false; message: string };

export interface ParseMoneyOptions {
  /** An empty string is invalid rather than 0 minor units — for a required amount, like a
   *  purchase order line's unit price. Most money fields in this app are optional and default to
   *  0, so this is `false` unless a caller opts in. */
  required?: boolean;
  /** The message when `required` is true and the input, once cleaned, is empty. */
  emptyMessage?: string;
  /** The message for anything that isn't a valid amount — wrong shape, negative, or more than two
   *  decimal places. */
  invalidMessage?: string;
}

const DEFAULT_INVALID_MESSAGE =
  "Enter a whole-currency amount, like 24000 or 24000.50. No more than two decimal places.";

/**
 * Read a typed amount into integer minor units, on the digits of the string. Spaces, commas and a
 * rupee sign are tolerated because people paste amounts; anything past two decimal places is
 * refused rather than rounded, and a negative amount is refused outright — this product has no
 * negative money field. Empty parses as zero unless `options.required` says otherwise.
 */
export function parseMinor(input: string, options: ParseMoneyOptions = {}): MoneyParse {
  const cleaned = input.replace(/[\s,₹]/g, "");
  if (cleaned === "") {
    if (options.required) {
      return { ok: false, message: options.emptyMessage ?? "Enter an amount." };
    }
    return { ok: true, minor: 0 };
  }

  const match = /^(\d+)(?:\.(\d{1,2}))?$/.exec(cleaned);
  if (!match) {
    return { ok: false, message: options.invalidMessage ?? DEFAULT_INVALID_MESSAGE };
  }
  const whole = match[1] ?? "0";
  const fraction = (match[2] ?? "").padEnd(2, "0");
  const minor = Number(whole) * 100 + Number(fraction);
  if (!Number.isSafeInteger(minor)) {
    return { ok: false, message: "That amount is too large." };
  }
  return { ok: true, minor };
}

/**
 * Read a typed **non-negative integer** — a quantity or a reorder point, never a fraction. Used
 * everywhere the server's field is a plain `int`, not `*_minor`.
 */
export function parseInt0(input: string): { ok: true; value: number } | { ok: false; message: string } {
  const cleaned = input.trim();
  if (cleaned === "") return { ok: true, value: 0 };
  if (!/^\d+$/.test(cleaned)) {
    return { ok: false, message: "Enter a whole number." };
  }
  const value = Number(cleaned);
  if (!Number.isSafeInteger(value)) return { ok: false, message: "That number is too large." };
  return { ok: true, value };
}
