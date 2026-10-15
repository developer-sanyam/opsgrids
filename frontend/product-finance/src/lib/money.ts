/**
 * Money helpers. Every amount in this product is an **integer number of minor units** (paise)
 * in a single ISO currency (INR) — never a float, never a `Numeric(_, 2)`. That is the platform
 * rule (see CLAUDE.md): arithmetic happens in whole paise, and we divide by 100 only at the last
 * moment, for display. The whole app renders amounts through these functions so the rupee columns
 * rule up to the paisa and rounding never drifts.
 */

import { currencyExponent, toMajor } from "@opsgrids/ui";

/** A monetary amount, in integer minor units (paise for INR). */
export type Minor = number;

export const CURRENCY = "INR";
const RUPEE = "₹"; // ₹

/** The sign shown before an amount: the rupee sign for INR, the symbol or code for anything else. */
function symbolFor(currency: string): string {
  if (currency === CURRENCY) return RUPEE;
  try {
    const parts = new Intl.NumberFormat("en-IN", { style: "currency", currency }).formatToParts(0);
    const symbol = parts.find((part) => part.type === "currency")?.value ?? currency;
    return /^[A-Za-z]+$/.test(symbol) ? `${symbol} ` : symbol;
  } catch {
    return `${currency} `;
  }
}

/** A number formatter for a currency's own fraction digits (INR 2, JPY 0, KWD 3). */
function numberFormat(places: number): Intl.NumberFormat {
  return new Intl.NumberFormat("en-IN", { minimumFractionDigits: places, maximumFractionDigits: places });
}

/** "₹12,34,567.00" — full precision with the Indian digit grouping and the currency sign. */
export function formatMoney(minor: Minor, currency: string = CURRENCY): string {
  return `${symbolFor(currency)}${formatAmount(minor, currency)}`;
}

/** "12,34,567.00" — same, without the currency sign (for right-aligned columns with a header unit). */
export function formatAmount(minor: Minor, currency: string = CURRENCY): string {
  return numberFormat(currencyExponent(currency)).format(toMajor(minor, currency));
}

/** "₹12,34,567" — whole units, no minor digits. For dense summaries where they are noise. */
export function formatMoneyWhole(minor: Minor, currency: string = CURRENCY): string {
  return `${symbolFor(currency)}${numberFormat(0).format(Math.round(toMajor(minor, currency)))}`;
}

/**
 * "₹96,760" / "₹12.35L" / "₹1.24Cr" — Indian compact notation for headline KPI figures. Below a
 * lakh the full grouped figure is shown (never "K", which Indian readers do not use); a lakh is
 * 1,00,000 and a crore is 1,00,00,000.
 */
export function formatMoneyCompact(minor: Minor, currency: string = CURRENCY): string {
  const major = toMajor(minor, currency);
  const magnitude = Math.abs(major);
  const sign = major < 0 ? "-" : "";
  const trimmed = (value: number) =>
    new Intl.NumberFormat("en-IN", { maximumFractionDigits: 2 }).format(value);
  if (magnitude >= 10_000_000) return `${sign}${symbolFor(currency)}${trimmed(magnitude / 10_000_000)}Cr`;
  if (magnitude >= 100_000) return `${sign}${symbolFor(currency)}${trimmed(magnitude / 100_000)}L`;
  return `${sign}${symbolFor(currency)}${numberFormat(0).format(Math.round(magnitude))}`;
}

/** A signed amount: "+₹4,500.00" / "−₹4,500.00" (true minus sign, never a hyphen). */
export function formatMoneySigned(minor: Minor, currency: string = CURRENCY): string {
  const sign = minor >= 0 ? "+" : "−";
  return `${sign}${formatMoney(Math.abs(minor), currency)}`;
}

/** Sum a list of minor amounts. Kept explicit so intent reads clearly at call sites. */
export function sumMinor(amounts: readonly Minor[]): Minor {
  return amounts.reduce((total, a) => total + a, 0);
}

/** The value a money input shows for an amount already stored: `2950` -> `"29.50"`. */
export function minorToInput(minor: Minor): string {
  const sign = minor < 0 ? "-" : "";
  const absolute = Math.abs(minor);
  const whole = Math.trunc(absolute / 100);
  const cents = absolute % 100;
  return `${sign}${whole}.${String(cents).padStart(2, "0")}`;
}

export type MoneyParse = { ok: true; minor: Minor } | { ok: false; message: string };

/**
 * Read a rupee amount typed into a form into integer minor units, on the string — never
 * `Math.round(parseFloat(x) * 100)`, which drifts once the amount is large enough. Spaces and
 * commas are tolerated (people paste amounts); anything past two decimal places is refused rather
 * than rounded away.
 */
export function parseMinor(input: string): MoneyParse {
  const cleaned = input.trim().replace(/[\s,₹]/g, "");
  if (cleaned === "") {
    return { ok: false, message: "Enter an amount." };
  }
  const match = /^(-)?(\d+)(?:\.(\d{1,2}))?$/.exec(cleaned);
  if (!match) {
    return {
      ok: false,
      message: "Enter a whole-rupee amount, like 24000 or 24000.50. No more than two decimal places.",
    };
  }
  const sign = match[1] ? -1 : 1;
  const whole = match[2] ?? "0";
  const fraction = (match[3] ?? "").padEnd(2, "0");
  const minor = sign * (Number(whole) * 100 + Number(fraction));
  if (!Number.isSafeInteger(minor)) {
    return { ok: false, message: "That amount is too large." };
  }
  return { ok: true, minor };
}

/** Read a typed non-negative quantity (fractional allowed — tonnes, cft). Empty is invalid. */
export function parseQuantity(input: string): { ok: true; value: number } | { ok: false; message: string } {
  const cleaned = input.trim();
  if (cleaned === "" || Number.isNaN(Number(cleaned))) {
    return { ok: false, message: "Enter a quantity." };
  }
  const value = Number(cleaned);
  if (!Number.isFinite(value) || value === 0) {
    return { ok: false, message: "Enter a non-zero quantity." };
  }
  return { ok: true, value };
}
