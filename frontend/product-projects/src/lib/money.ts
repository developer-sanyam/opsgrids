/**
 * Money, in and out of a form.
 *
 * Amounts are integer minor units with an ISO currency everywhere except the digits inside a
 * money input (ADR: no floats, no `Numeric(_, 2)`). The parse works on the digits of the typed
 * string rather than `Math.round(parseFloat(x) * 100)` — `29.35 * 100` is `2934.9999999999995`,
 * and rounding that away is the kind of fix that works until the amount is large enough that it
 * doesn't.
 */

/** The value a money input shows for an amount already stored: `2950` → `"29.50"`. */
export function minorToInput(minor: number): string {
  const sign = minor < 0 ? "-" : "";
  const absolute = Math.abs(minor);
  const whole = Math.trunc(absolute / 100);
  const cents = absolute % 100;
  return cents === 0 ? `${sign}${whole}` : `${sign}${whole}.${String(cents).padStart(2, "0")}`;
}

export type MoneyParse = { ok: true; minor: number } | { ok: false; message: string };

/**
 * Read a typed amount into integer minor units, on the string. Spaces and commas are tolerated
 * because people paste amounts; anything past two decimal places is refused rather than rounded.
 * Empty parses as zero — every money field on this product defaults to 0, never null.
 */
export function parseMinor(input: string): MoneyParse {
  const cleaned = input.replace(/[\s,]/g, "");
  if (cleaned === "") return { ok: true, minor: 0 };

  const match = /^(\d+)(?:\.(\d{1,2}))?$/.exec(cleaned);
  if (!match) {
    return {
      ok: false,
      message:
        "Enter a whole-currency amount, like 24000 or 24000.50. No more than two decimal places.",
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
