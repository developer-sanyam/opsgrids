/**
 * Money helpers. Every amount in this product is an **integer number of minor units** (paise)
 * in a single ISO currency (INR) — never a float, never a `Numeric(_, 2)`. That is the platform
 * rule (see CLAUDE.md): arithmetic happens in whole paise, and we divide by 100 only at the last
 * moment, for display. The whole app renders amounts through these functions so the rupee columns
 * rule up to the paisa and rounding never drifts.
 */

/** A monetary amount, in integer minor units (paise for INR). */
export type Minor = number;

export const CURRENCY = "INR";
const RUPEE = "₹"; // ₹

const FULL = new Intl.NumberFormat("en-IN", {
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

const WHOLE = new Intl.NumberFormat("en-IN", {
  minimumFractionDigits: 0,
  maximumFractionDigits: 0,
});

const COMPACT = new Intl.NumberFormat("en-IN", {
  notation: "compact",
  maximumFractionDigits: 2,
});

/** "₹12,34,567.00" — full precision with the Indian digit grouping and the rupee sign. */
export function formatMoney(minor: Minor): string {
  return `${RUPEE}${FULL.format(minor / 100)}`;
}

/** "12,34,567.00" — same, without the currency sign (for right-aligned columns with a header unit). */
export function formatAmount(minor: Minor): string {
  return FULL.format(minor / 100);
}

/** "₹12,34,567" — whole rupees, no paise. For dense summaries where the paise are noise. */
export function formatMoneyWhole(minor: Minor): string {
  return `${RUPEE}${WHOLE.format(Math.round(minor / 100))}`;
}

/** "₹12.35L" / "₹1.24Cr" — Indian compact notation for headline KPI figures. */
export function formatMoneyCompact(minor: Minor): string {
  return `${RUPEE}${COMPACT.format(minor / 100)}`;
}

/** A signed amount: "+₹4,500.00" / "−₹4,500.00" (true minus sign, never a hyphen). */
export function formatMoneySigned(minor: Minor): string {
  const sign = minor >= 0 ? "+" : "−";
  return `${sign}${formatMoney(Math.abs(minor))}`;
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
