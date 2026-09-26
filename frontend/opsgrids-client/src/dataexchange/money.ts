/**
 * Money in a sheet is a decimal in major units; the API takes integer minor units. The conversion
 * is string arithmetic on the digits the user typed, never a float multiplication: `19.99 * 100`
 * is 1998.9999999999998 in IEEE 754, and an invoice that is a paisa out is found in a dispute.
 *
 * The exponent table mirrors `libs/platform-core/src/platform_core/money.py`; extend both together.
 */

import { RowProblem } from "./types";

const EXPONENTS: ReadonlyMap<string, number> = new Map([
  ["JPY", 0],
  ["KRW", 0],
  ["VND", 0],
  ["CLP", 0],
  ["ISK", 0],
  ["BHD", 3],
  ["KWD", 3],
  ["OMR", 3],
  ["TND", 3],
  ["JOD", 3],
]);
const DEFAULT_EXPONENT = 2;

/** Number of decimal places in `currency`'s minor unit. */
export function currencyExponent(currency: string): number {
  return EXPONENTS.get(currency.trim().toUpperCase()) ?? DEFAULT_EXPONENT;
}

/**
 * A number as plain decimal digits, never in exponent notation: `1e21` is `"1000000000000000000000"`
 * and `59.970000000000006` is `"59.97"`. A safe integer is exact; any other number is rounded to
 * the 15 significant digits Excel itself keeps, which removes float noise left by a formula.
 * Null for NaN and the infinities.
 */
export function plainNumberString(value: number): string | null {
  if (!Number.isFinite(value)) return null;
  if (Number.isSafeInteger(value)) return String(value);
  const [mantissa = "", exponentPart = "0"] = Math.abs(value).toExponential(14).split("e");
  const digits = mantissa.replace(".", "").replace(/0+$/, "") || "0";
  const pointAt = Number(exponentPart) + 1;
  let plain: string;
  if (pointAt <= 0) plain = `0.${"0".repeat(-pointAt)}${digits}`;
  else if (digits.length <= pointAt) plain = digits + "0".repeat(pointAt - digits.length);
  else plain = `${digits.slice(0, pointAt)}.${digits.slice(pointAt)}`;
  return value < 0 ? `-${plain}` : plain;
}

export type DecimalProblem = "not-a-number" | "exponent" | "separator";

export type DecimalParts =
  | { ok: true; negative: boolean; whole: string; fraction: string }
  | { ok: false; problem: DecimalProblem };

/** Splits `12.50`, `.5` or `-3` into its digits. Rejects `1e5`, `1,000`, `+5`, `5.` and text. */
export function splitDecimal(text: string): DecimalParts {
  const s = text.trim();
  if (/^[+-]?(?:\d+\.?\d*|\.\d+)[eE][+-]?\d+$/.test(s)) return { ok: false, problem: "exponent" };
  if (/^-?\d[\d,_\s]*(?:\.\d+)?$/.test(s) && /[,_\s]/.test(s)) {
    return { ok: false, problem: "separator" };
  }
  const match = /^(-)?(?:(\d+)(?:\.(\d+))?|\.(\d+))$/.exec(s);
  if (!match) return { ok: false, problem: "not-a-number" };
  return {
    ok: true,
    negative: match[1] === "-",
    whole: match[2] ?? "0",
    fraction: match[3] ?? match[4] ?? "",
  };
}

/** Compares two plain decimal strings exactly: negative, zero or positive like `a - b`. */
export function compareDecimals(a: string, b: string): number {
  const left = splitDecimal(a);
  const right = splitDecimal(b);
  if (!left.ok || !right.ok) throw new RangeError(`Not a plain decimal: ${left.ok ? b : a}`);
  const scale = Math.max(left.fraction.length, right.fraction.length);
  const scaled = (parts: { negative: boolean; whole: string; fraction: string }): bigint => {
    const magnitude = BigInt(parts.whole + parts.fraction.padEnd(scale, "0"));
    return parts.negative ? -magnitude : magnitude;
  };
  const difference = scaled(left) - scaled(right);
  if (difference === 0n) return 0;
  return difference < 0n ? -1 : 1;
}

export type MoneyProblem = DecimalProblem | "empty" | "negative" | "decimals" | "too-large";

export type MajorUnits =
  | { ok: true; minor: number; major: string }
  | { ok: false; problem: MoneyProblem };

/** The reason a major-unit amount was refused, as the tail of a sentence ("Price <reason>"). */
export function describeMoneyProblem(problem: MoneyProblem, currency: string): string {
  const places = currencyExponent(currency);
  switch (problem) {
    case "empty":
    case "not-a-number":
      return "must be a plain number, for example 1499.50";
    case "negative":
      return "cannot be negative";
    case "exponent":
      return "must be written out in full, not in exponent form such as 1e5";
    case "separator":
      return "must be a plain number with a dot for decimals and no commas or spaces, for example 1499.50";
    case "decimals":
      return places === 0
        ? `must be a whole number for ${currency.toUpperCase()}, which has no decimal places`
        : `can have at most ${places} decimal ${places === 1 ? "place" : "places"} for ${currency.toUpperCase()}`;
    case "too-large":
      return "is too large";
  }
}

/**
 * A major-unit amount (`"1499.50"` or `1499.5`) as integer minor units and the exact major string.
 * Refuses rather than rounds: negatives, exponent notation, thousands separators, more decimals
 * than the currency has (trailing zeros are fine: `100.500` is 100.50), and anything non-numeric.
 */
export function parseMajorUnits(input: string | number, currency: string): MajorUnits {
  const text = typeof input === "number" ? plainNumberString(input) : input.trim();
  if (text === null) return { ok: false, problem: "not-a-number" };
  if (text === "") return { ok: false, problem: "empty" };
  const parts = splitDecimal(text);
  if (!parts.ok) return { ok: false, problem: parts.problem };
  if (parts.negative) return { ok: false, problem: "negative" };
  const places = currencyExponent(currency);
  const kept = parts.fraction.slice(0, places);
  if (/[^0]/.test(parts.fraction.slice(places))) return { ok: false, problem: "decimals" };
  const minor = BigInt(parts.whole + kept.padEnd(places, "0"));
  if (minor > BigInt(Number.MAX_SAFE_INTEGER)) return { ok: false, problem: "too-large" };
  const value = Number(minor);
  return { ok: true, minor: value, major: minorToMajor(value, currency) };
}

/**
 * Converts a major-unit amount to integer minor units for a create payload. Throws a `RowProblem`
 * (so a `toPayload` that calls it fails one row with a sentence) when the amount is not exact.
 */
export function majorToMinor(input: string | number, currency: string): number {
  const result = parseMajorUnits(input, currency);
  if (!result.ok) throw new RowProblem(`The amount ${describeMoneyProblem(result.problem, currency)}`);
  return result.minor;
}

/** Integer minor units as an exact major-unit decimal string: `12345` INR is `"123.45"`, JPY `"12345"`. */
export function minorToMajor(minor: number, currency: string): string {
  if (!Number.isSafeInteger(minor)) throw new RangeError("Minor units must be a whole number");
  const places = currencyExponent(currency);
  const digits = String(Math.abs(minor)).padStart(places + 1, "0");
  const whole = digits.slice(0, digits.length - places);
  const fraction = digits.slice(digits.length - places);
  return `${minor < 0 ? "-" : ""}${whole}${places > 0 ? `.${fraction}` : ""}`;
}
