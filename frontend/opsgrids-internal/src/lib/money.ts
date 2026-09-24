/**
 * Money formatting. Amounts cross the wire as **integer minor units** with an ISO-4217 currency
 * (ADR-0000 §8 / ADR-0004) — never a float, never `Numeric`. These helpers are the only place a
 * minor-unit integer becomes a human string, so the exponent handling lives in one spot.
 */

/** Minor-unit exponent per ISO-4217 currency. Extend as new currencies appear on the platform. */
const EXPONENT: Record<string, number> = {
  INR: 2,
  USD: 2,
  EUR: 2,
  GBP: 2,
  AED: 2,
  SGD: 2,
  JPY: 0,
  KWD: 3,
};

function exponentFor(currency: string): number {
  return EXPONENT[currency] ?? 2;
}

/** Minor units → major number (e.g. 1_20_000_00 paise → 120000). */
export function toMajor(minor: number, currency: string): number {
  return minor / 10 ** exponentFor(currency);
}

interface MoneyOptions {
  /** Show minor-unit fraction digits (invoice lines). Off by default for scannable columns. */
  precise?: boolean;
  /** Compact Indian/Western notation (₹18.4L, $1.2M) for headline metrics. */
  compact?: boolean;
}

/**
 * Format a minor-unit amount for display. Default: grouped, no fraction digits — the form money
 * columns want. `precise` adds the currency's fraction digits; `compact` uses short notation.
 */
export function formatMoney(
  minor: number,
  currency: string,
  options: MoneyOptions = {},
): string {
  const exp = exponentFor(currency);
  const major = toMajor(minor, currency);
  const { precise = false, compact = false } = options;

  const fractionDigits = precise ? exp : 0;
  try {
    return new Intl.NumberFormat("en-IN", {
      style: "currency",
      currency,
      minimumFractionDigits: fractionDigits,
      maximumFractionDigits: fractionDigits,
      ...(compact ? { notation: "compact" } : {}),
    }).format(major);
  } catch {
    // Unknown currency code — fall back to a plain grouped number with the code appended.
    return `${new Intl.NumberFormat("en-IN").format(major)} ${currency}`;
  }
}

/** "₹4,50,000 / mo" — a monthly recurring amount. */
export function formatMonthly(minor: number, currency: string): string {
  return `${formatMoney(minor, currency)} / mo`;
}

/**
 * What an operator typed into an amount field, as **integer minor units** — or `null` when it is
 * not an amount: no digits, a sign, a thousands separator, or more decimal places than the
 * currency has. It does the sum on digit strings rather than `Number(text) * 100`, because a float
 * gets `1.005 * 100` wrong and money is never a float (ADR-0000 §7).
 *
 * Zero is a number, so it parses; whether zero is a valid *grant* is the caller's rule.
 */
export function parseMinorUnits(text: string, currency: string): number | null {
  const exponent = exponentFor(currency);
  const match = /^(\d+)(?:\.(\d+))?$/.exec(text.trim());
  if (match === null) return null;
  const [, whole, fraction = ""] = match;
  if (whole === undefined || fraction.length > exponent) return null;
  const minor = Number(whole) * 10 ** exponent + Number(fraction.padEnd(exponent, "0") || "0");
  return Number.isSafeInteger(minor) ? minor : null;
}
