/**
 * Exact money entry and display for the catalogue editor — **no floating point anywhere**.
 *
 * Amounts cross the wire as integer minor units (`amount_minor`) and usage rates as integer micros
 * of a major unit (`unit_price_micros`, ADR-0000 §8). A price an operator types is a decimal
 * string; turning it into minor units with `Number(x) * 100` is wrong twice — it assumes two
 * decimal places (JPY has none, KWD has three) and it rounds through binary floating point
 * (`19.99 * 100 === 1998.9999999999998`). Everything here works on the digits as text and on
 * `BigInt`, and a value is turned into a JSON number only once it is proven to be a safe integer.
 *
 * `SELLABLE_CURRENCIES` mirrors catalog's `domain.SELLABLE_CURRENCIES` (the server refuses to
 * publish in anything else) and the exponents mirror `platform_core.money`.
 */

/** Currency → number of minor-unit decimal places. The publishable set, server-mirrored. */
export const CURRENCY_EXPONENTS: Readonly<Record<string, number>> = {
  INR: 2,
  USD: 2,
  EUR: 2,
  GBP: 2,
  AED: 2,
  SGD: 2,
  AUD: 2,
  CAD: 2,
  JPY: 0,
  KWD: 3,
};

export const SELLABLE_CURRENCIES: readonly string[] = Object.keys(CURRENCY_EXPONENTS);

/** Micros per major unit — a usage rate's precision. */
export const MICROS_DIGITS = 6;

const MAX_SAFE = BigInt(Number.MAX_SAFE_INTEGER);

export function exponentOf(currency: string): number {
  return CURRENCY_EXPONENTS[currency.toUpperCase()] ?? 2;
}

export type Parsed = { ok: true; value: number } | { ok: false; error: string };

/** A whole part grouped in thousands (1,250,000) or in the Indian lakh style (12,50,000). */
const WESTERN_GROUPS = /^\d{1,3}(?:[,\s_]\d{3})+$/;
const INDIAN_GROUPS = /^\d{1,2}(?:[,\s_]\d{2})*[,\s_]\d{3}$/;
const SEPARATOR = /[,\s_]/;

/**
 * A decimal string with at most `digits` fraction digits, as an integer count of 10^-digits.
 *
 * A separator (`,`, a space, `_`) is accepted **only where it is a real group separator** in the
 * whole part — `1,250.50`, `12,50,000` — and never in the fraction. Stripping every comma would
 * read a decimal comma as a group separator and scale the value silently: "1,99" EUR would be
 * EUR 199.00 and a "0,0025" rate 25 per unit. Those are refused instead. `grouping: false` (a usage
 * rate) refuses every separator. Signs, exponents and anything else are refused too.
 */
function parseScaled(text: string, digits: number, noun: string, grouping = true): Parsed {
  const trimmed = text.trim();
  if (trimmed === "") return { ok: false, error: `Enter ${noun}.` };
  const [wholeRaw = "", ...rest] = trimmed.split(".");
  const fractionRaw = rest.join(".");
  if (SEPARATOR.test(wholeRaw) || SEPARATOR.test(fractionRaw)) {
    const grouped = grouping && !SEPARATOR.test(fractionRaw) && rest.length <= 1;
    if (!grouped || !(WESTERN_GROUPS.test(wholeRaw) || INDIAN_GROUPS.test(wholeRaw))) {
      return {
        ok: false,
        error: grouping
          ? `Use a dot for decimals, like 1250.50. A comma may only group thousands (1,250.50).`
          : `Use a dot for decimals, like 0.0025, with no commas or spaces.`,
      };
    }
  }
  const cleaned = trimmed.replace(/[,\s_]/g, "");
  const match = /^(\d+)(?:\.(\d*))?$/.exec(cleaned);
  if (!match) return { ok: false, error: `Enter ${noun} as digits, like 1250 or 1250.50.` };
  const whole = match[1] ?? "0";
  const fraction = match[2] ?? "";
  if (fraction.length > digits) {
    return {
      ok: false,
      error:
        digits === 0
          ? `This currency has no minor unit — enter a whole number.`
          : `Use at most ${digits} decimal place${digits === 1 ? "" : "s"}.`,
    };
  }
  const scaled = BigInt(whole) * 10n ** BigInt(digits) + BigInt(fraction.padEnd(digits, "0") || "0");
  if (scaled > MAX_SAFE) return { ok: false, error: `That is too large.` };
  return { ok: true, value: Number(scaled) };
}

/** "1,250.50" in INR → 125050 (minor units). JPY refuses a fraction; KWD takes three digits. */
export function parseAmountToMinor(text: string, currency: string): Parsed {
  return parseScaled(text, exponentOf(currency), "an amount");
}

/**
 * A usage rate in major units, to micros: "0.0025" → 2500. Up to six decimal places. No separator
 * is accepted at all: a rate is never large enough to need grouping, and "0,0025" read as grouped
 * would be 10,000 times the rate.
 */
export function parseRateToMicros(text: string): Parsed {
  return parseScaled(text, MICROS_DIGITS, "a rate", false);
}

/** The inverse of `parseScaled`: an integer count of 10^-digits as a plain decimal string. */
function formatScaled(value: number | bigint, digits: number): string {
  const big = BigInt(value);
  const negative = big < 0n;
  const abs = negative ? -big : big;
  const text = abs.toString().padStart(digits + 1, "0");
  const whole = digits === 0 ? text : text.slice(0, -digits);
  const fraction = digits === 0 ? "" : text.slice(-digits);
  return `${negative ? "-" : ""}${whole}${fraction ? `.${fraction}` : ""}`;
}

/** 125050 INR → "1250.50": the editor's prefill, round-trippable through `parseAmountToMinor`. */
export function minorToInput(minor: number, currency: string): string {
  return formatScaled(minor, exponentOf(currency));
}

/** 2500 micros → "0.0025", trailing zeros trimmed. */
export function microsToInput(micros: number): string {
  const text = formatScaled(micros, MICROS_DIGITS);
  return text.includes(".") ? text.replace(/0+$/, "").replace(/\.$/, "") : text;
}

/** Grouped for reading, with the code: "INR 1,250.50", "JPY 1,200", "KWD 1.250". */
export function formatMinor(minor: number, currency: string): string {
  const [whole = "0", fraction] = minorToInput(minor, currency).split(".");
  const grouped = whole.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  return `${currency} ${grouped}${fraction !== undefined ? `.${fraction}` : ""}`;
}

/** A usage rate for reading: "INR 0.0025". */
export function formatMicros(micros: number, currency: string): string {
  return `${currency} ${microsToInput(micros)}`;
}
