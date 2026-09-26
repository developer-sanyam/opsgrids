/**
 * Money, in and out of a form.
 *
 * An asset's cost is integer minor units with an ISO currency everywhere except the digits
 * inside the input (CLAUDE.md: no floats, no `Numeric(_, 2)`). The number of decimals is the
 * currency's own (none for JPY, three for KWD, two otherwise); the exponent table and the string
 * arithmetic that applies it live in `@opsgrids/client`, which the Excel import uses too, so a
 * cost of 150000 yen is 150000 minor units on the wire, in a sheet and on screen.
 *
 * Nothing here multiplies a float: `29.35 * 100` is `2934.9999999999995`, and rounding that away
 * is the kind of fix that works until the amount is large enough that it doesn't.
 */

import { majorToMinor, minorToMajor } from "@opsgrids/client";

/** Decimal places in `currency`'s minor unit: the digits after the point of `minorToMajor(0, …)`. */
export function currencyDecimals(currency: string): number {
  return (minorToMajor(0, currency).split(".")[1] ?? "").length;
}

/**
 * The value a money input shows for an amount already stored: `2950` INR is `"29.50"`, a whole
 * amount drops its decimals (`21900000` INR is `"219000"`), `150000` JPY is `"150000"` and `1250`
 * KWD is `"1.250"`.
 */
export function minorToInput(minor: number, currency: string): string {
  return minorToMajor(minor, currency).replace(/\.0+$/, "");
}

export type MoneyParse = { ok: true; minor: number } | { ok: false; message: string };

const NUMBER_WORDS = ["no", "one", "two", "three"];

function amountMessage(currency: string): string {
  const places = currencyDecimals(currency);
  if (places === 0) {
    return `Enter a whole-currency amount, like 24000. ${currency.toUpperCase()} has no decimal places.`;
  }
  const example = `24000.${"50".padEnd(places, "0")}`;
  const word = NUMBER_WORDS[places] ?? String(places);
  return `Enter a whole-currency amount, like 24000 or ${example}. No more than ${word} decimal places.`;
}

/**
 * Read a typed amount into integer minor units, in `currency`. Spaces and commas are tolerated
 * because people paste amounts; anything past the currency's decimal places is refused rather
 * than rounded. Empty parses as zero — the cost field defaults to 0, never null.
 */
export function parseMinor(input: string, currency: string): MoneyParse {
  const cleaned = input.replace(/[\s,]/g, "");
  if (cleaned === "") return { ok: true, minor: 0 };

  const match = /^\d+(?:\.(\d+))?$/.exec(cleaned);
  if (!match || (match[1] ?? "").length > currencyDecimals(currency)) {
    return { ok: false, message: amountMessage(currency) };
  }
  try {
    return { ok: true, minor: majorToMinor(cleaned, currency) };
  } catch {
    // The digits are well formed and fit the currency, so the only refusal left is size.
    return { ok: false, message: "That amount is too large." };
  }
}
