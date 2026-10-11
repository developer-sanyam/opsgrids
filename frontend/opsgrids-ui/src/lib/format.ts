/**
 * The formatters the design system itself needs, and money, which every product shows the same
 * way. Other product formatting — dates, SLA clocks, domain labels — stays in the app that owns
 * those rules.
 */

/** Initials for an avatar fallback: "Sofia Marchetti" -> "SM". */
export function initials(name: string): string {
  const parts = name.trim().split(/\s+/);
  const first = parts[0]?.[0] ?? "";
  const last = parts.length > 1 ? (parts[parts.length - 1]?.[0] ?? "") : "";
  return (first + last).toUpperCase();
}

/** A signed percentage for a delta pill: `4.2` -> "+4.2%", `-1.35` -> "−1.4%" (true minus). */
export function formatDelta(pct: number): string {
  const sign = pct >= 0 ? "+" : "−";
  return `${sign}${Math.abs(pct).toFixed(1)}%`;
}

/*
 * Money. Amounts are integer minor units with an ISO currency everywhere (CLAUDE.md: no floats,
 * no `Numeric(_, 2)`). Division happens only for display; a typed amount is read on its digits,
 * never through `parseFloat` (`29.35 * 100` is `2934.9999999999995`).
 */

const exponents = new Map<string, number>();

/** The currency's minor-unit digits, from `Intl` (INR 2, JPY 0, KWD 3); 2 for a code it cannot read. */
export function currencyExponent(currency: string): number {
  let exponent = exponents.get(currency);
  if (exponent === undefined) {
    try {
      exponent =
        new Intl.NumberFormat("en", { style: "currency", currency }).resolvedOptions().maximumFractionDigits ?? 2;
    } catch {
      exponent = 2;
    }
    exponents.set(currency, exponent);
  }
  return exponent;
}

/** Minor units to a major number, for display only: `12345` INR is `123.45`. */
export function toMajor(minor: number, currency: string): number {
  return minor / 10 ** currencyExponent(currency);
}

export interface MoneyOptions {
  /** Show the currency's fraction digits (invoice lines). Off by default: whole units scan faster. */
  precise?: boolean | undefined;
  /** Short form for tight surfaces: crore and lakh for INR ("₹3.2 Cr"), K, M and B otherwise. */
  compact?: boolean | undefined;
  /** Grouping and symbol locale. Default `en-IN`. */
  locale?: string | undefined;
}

function currencySymbol(currency: string, locale: string): string {
  try {
    const parts = new Intl.NumberFormat(locale, { style: "currency", currency }).formatToParts(0);
    const symbol = parts.find((part) => part.type === "currency")?.value ?? currency;
    return /^[A-Za-z]+$/.test(symbol) ? `${symbol} ` : symbol;
  } catch {
    return `${currency} `;
  }
}

/** One decimal, dropping a trailing ".0": 3.0 -> "3", 3.24 -> "3.2". */
function oneDecimal(n: number): string {
  return n.toFixed(1).replace(/\.0$/, "");
}

function compactMoney(major: number, currency: string, locale: string): string {
  const sign = major < 0 ? "-" : "";
  const size = Math.abs(major);
  const symbol = currencySymbol(currency, locale);
  const steps: [number, string][] =
    currency === "INR"
      ? [
          [1e7, " Cr"],
          [1e5, " L"],
        ]
      : [
          [1e9, "B"],
          [1e6, "M"],
          [1e3, "K"],
        ];
  for (const [unit, suffix] of steps) {
    if (size >= unit) return `${sign}${symbol}${oneDecimal(size / unit)}${suffix}`;
  }
  return `${sign}${symbol}${Math.round(size).toLocaleString(locale)}`;
}

/**
 * A minor-unit amount for display: `formatMoney(1_250_000_00, "INR")` is "₹12,50,000". Never sum
 * amounts of different currencies before calling this; show one line per currency instead.
 */
export function formatMoney(minor: number, currency: string, options: MoneyOptions = {}): string {
  const { precise = false, compact = false, locale = "en-IN" } = options;
  const major = toMajor(minor, currency);
  if (compact) return compactMoney(major, currency, locale);
  const digits = precise ? currencyExponent(currency) : 0;
  try {
    return new Intl.NumberFormat(locale, {
      style: "currency",
      currency,
      minimumFractionDigits: digits,
      maximumFractionDigits: digits,
    }).format(major);
  } catch {
    return `${new Intl.NumberFormat(locale, { maximumFractionDigits: digits }).format(major)} ${currency}`;
  }
}

export type MoneyParse = { ok: true; minor: number } | { ok: false; message: string };

function acceptedForm(currency: string, exponent: number): string {
  if (exponent === 0) return `Enter a whole amount in digits, like 24000. ${currency} has no decimal places.`;
  const example = `24000.${"5".padEnd(exponent, "0")}`;
  const places = ["one", "two", "three"][exponent - 1] ?? String(exponent);
  return `Enter an amount in digits, like 24000 or ${example}, with no currency sign or words and no more than ${places} decimal places.`;
}

/**
 * A typed amount in integer minor units, read on its digits. Spaces and commas are tolerated
 * because people paste grouped amounts ("1,20,000.50"); anything else — a currency sign, a word,
 * a comma decimal, a minus, more decimals than the currency has — is refused with a sentence that
 * says what is accepted, never read as some other amount. A blank is zero.
 */
export function parseMinor(input: string, currency: string): MoneyParse {
  const exponent = currencyExponent(currency);
  const cleaned = input.replace(/[\s,]/g, "");
  if (cleaned === "") return { ok: true, minor: 0 };

  const match = /^(\d+)(?:\.(\d+))?$/.exec(cleaned);
  const fraction = match?.[2] ?? "";
  if (!match || fraction.length > exponent || (match[2] !== undefined && exponent === 0)) {
    return { ok: false, message: acceptedForm(currency, exponent) };
  }

  const minor = Number(`${match[1] ?? "0"}${fraction.padEnd(exponent, "0")}`);
  if (!Number.isSafeInteger(minor)) return { ok: false, message: "That amount is too large." };
  return { ok: true, minor };
}

/** What a money input shows for a stored amount: `2950` INR is "29.50", `2400000` is "24000". */
export function minorToInput(minor: number, currency: string): string {
  const exponent = currencyExponent(currency);
  const sign = minor < 0 ? "-" : "";
  const digits = String(Math.abs(minor)).padStart(exponent + 1, "0");
  const whole = digits.slice(0, digits.length - exponent);
  const fraction = digits.slice(digits.length - exponent);
  return /^0*$/.test(fraction) ? `${sign}${whole}` : `${sign}${whole}.${fraction}`;
}
